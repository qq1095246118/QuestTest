from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.db_accuracy.binance_source import BinanceSource
from tests.db_accuracy.cache_models import (
    CachedCompareRequest,
    CachedRunResult,
    CachedShardResult,
    MarketShard,
)
from tests.db_accuracy.cache_store import CacheStore
from tests.db_accuracy.cached_db_reader import CachedDBReader
from tests.db_accuracy.cached_source import CachedBinanceSource
from tests.db_accuracy.datacompy_engine import DataComPyEngine
from tests.db_accuracy.db_reader import DBAccuracyReader
from tests.db_accuracy.frame_normalizer import rows_to_normalized_frame
from tests.db_accuracy.models import ResolvedTableSpec, TableSpec
from tests.db_accuracy.shard_planner import (
    explicit_market_key,
    split_time_partitions,
    validate_cached_request,
)
from tests.db_accuracy.table_specs import load_table_specs, resolve_spec


SOURCE_FAILURE_STATUSES = {"source_market_unavailable", "source_request_failed"}


class CachedAccuracyRunner:
    def __init__(self, db: Any = None, source: Any = None) -> None:
        if db is None:
            from infrastructure.database.db_client import DBClient

            db = DBClient()
        self.db = db
        self.source = source if source is not None else BinanceSource()

    def run(self, request: CachedCompareRequest) -> CachedRunResult:
        try:
            validate_cached_request(request)
            spec = _find_spec(request.table)
            columns = DBAccuracyReader(self.db).table_columns(spec.table)
            resolved = resolve_spec(spec, columns)
            if resolved.time_field is None:
                raise ValueError(
                    f"{request.table} has no time field for cached comparison"
                )

            db_reader = CachedDBReader(self.db)
            cached_source = CachedBinanceSource(
                store=CacheStore(request.cache_root),
                source=self.source,
            )
            cache_root = Path(request.cache_root)
            report_root = _run_report_root(cache_root)
            engine = DataComPyEngine(report_root=report_root)
            shards = _build_shards(resolved, request, db_reader)
            partitions = split_time_partitions(
                request.start_ms,
                request.end_ms,
                request.partition_days,
            )
        except Exception as exc:  # noqa: BLE001 - runner reports setup failures
            return _setup_failure_result(request, exc)

        result = CachedRunResult()
        for shard in shards:
            for partition in partitions:
                try:
                    source_frame, manifest = cached_source.ensure_partition(
                        resolved.spec,
                        shard,
                        partition,
                        refresh=request.refresh_cache,
                    )
                    db_rows = db_reader.rows_for_partition(shard, partition)
                    db_frame = rows_to_normalized_frame(shard, db_rows)

                    if manifest.status in SOURCE_FAILURE_STATUSES:
                        result.shards.append(
                            CachedShardResult(
                                shard_label=shard.label,
                                partition_label=partition.label,
                                status="failed",
                                db_rows=db_frame.height,
                                source_rows=source_frame.height,
                                differences=_source_failure_differences(
                                    manifest.status,
                                    db_frame.height,
                                ),
                                report_path=None,
                                diff_path=None,
                                message=_source_failure_message(
                                    manifest.status,
                                    manifest.source_error,
                                ),
                            )
                        )
                        continue

                    shard_result = engine.compare(
                        shard_label=shard.label,
                        partition_label=partition.label,
                        db_frame=db_frame,
                        source_frame=source_frame,
                        join_columns=shard.join_columns,
                    )
                    result.shards.append(
                        _with_cache_relative_artifact_paths(
                            shard_result,
                            report_root=report_root,
                            cache_root=cache_root,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - runner returns shard failures
                    result.shards.append(
                        CachedShardResult(
                            shard_label=shard.label,
                            partition_label=partition.label,
                            status="failed",
                            db_rows=0,
                            source_rows=0,
                            differences=1,
                            report_path=None,
                            diff_path=None,
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )

        return result


def cached_result_to_json(result: CachedRunResult) -> str:
    return json.dumps(
        {
            "passed": result.passed,
            "summary": result.summary_text(),
            "shards": [asdict(shard) for shard in result.shards],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _find_spec(table: str) -> TableSpec:
    for spec in load_table_specs():
        if spec.table == table:
            return spec
    raise ValueError(f"unknown table for cached comparison: {table}")


def _build_shards(
    resolved: ResolvedTableSpec,
    request: CachedCompareRequest,
    db_reader: CachedDBReader,
) -> list[MarketShard]:
    explicit = explicit_market_key(resolved, request)
    if explicit is not None:
        keys = [explicit]
    else:
        keys = db_reader.discover_market_keys(
            table=resolved.spec.table,
            key_fields=resolved.key_fields,
            time_field=_required_time_field(resolved),
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            filters=_discovery_filters(resolved, request),
            limit=request.max_shards,
        )

    if not keys:
        raise ValueError("no_shards_discovered")

    return [
        MarketShard(
            table=resolved.spec.table,
            endpoint=resolved.spec.endpoint,
            kind=resolved.spec.kind,
            key_values=dict(key),
            time_field=_required_time_field(resolved),
            source_time_field=(
                resolved.spec.source_time_field or _required_time_field(resolved)
            ),
            compare_fields=resolved.compare_fields,
            request_limit=resolved.spec.request_limit,
        )
        for key in keys
    ]


def _discovery_filters(
    resolved: ResolvedTableSpec,
    request: CachedCompareRequest,
) -> dict[str, Any]:
    candidates = {
        "symbol": request.symbols,
        "pair": request.pairs,
        "contract_type": request.contract_types,
        "interval": request.intervals,
    }
    filters: dict[str, Any] = {}
    for field, values in candidates.items():
        if field not in resolved.key_fields or not values:
            continue
        if len(values) > 1:
            raise ValueError(
                "multi-value discovery filters are unsupported in this version: "
                f"{field}"
            )
        filters[field] = values[0]
    return filters


def _required_time_field(resolved: ResolvedTableSpec) -> str:
    if resolved.time_field is None:
        raise ValueError(f"{resolved.spec.table} has no time field for cached comparison")
    return resolved.time_field


def _source_failure_message(status: str, source_error: str | None) -> str:
    if source_error:
        return f"{status}: {source_error}"
    return status


def _setup_failure_result(request: CachedCompareRequest, exc: Exception) -> CachedRunResult:
    return CachedRunResult(
        shards=[
            CachedShardResult(
                shard_label=f"table={request.table}",
                partition_label="setup",
                status="failed",
                db_rows=0,
                source_rows=0,
                differences=1,
                report_path=None,
                diff_path=None,
                message=f"setup_error:{type(exc).__name__}:{exc}",
            )
        ]
    )


def _source_failure_differences(status: str, db_rows: int) -> int:
    if status == "source_request_failed":
        return 1
    return max(db_rows, 1)


def _run_report_root(cache_root: Path) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return cache_root / "reports" / f"run_id={run_id}"


def _with_cache_relative_artifact_paths(
    result: CachedShardResult,
    report_root: Path,
    cache_root: Path,
) -> CachedShardResult:
    return replace(
        result,
        report_path=_cache_relative_artifact_path(
            result.report_path,
            report_root=report_root,
            cache_root=cache_root,
        ),
        diff_path=_cache_relative_artifact_path(
            result.diff_path,
            report_root=report_root,
            cache_root=cache_root,
        ),
    )


def _cache_relative_artifact_path(
    path: str | None,
    report_root: Path,
    cache_root: Path,
) -> str | None:
    if path is None:
        return path
    absolute_path = report_root / path
    return str(absolute_path.relative_to(cache_root))
