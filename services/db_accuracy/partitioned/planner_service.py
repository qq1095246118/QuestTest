"""统一分区 DB accuracy 任务规划服务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from services.db_accuracy.cached.cached_db_reader_service import CachedDBReaderService
from services.db_accuracy.cached.shard_planner_service import split_time_partitions
from services.db_accuracy.db_reader_service import DBAccuracyReaderService
from services.db_accuracy.models import ResolvedTableSpec, TableSpec
from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    PartitionedAccuracyRequest,
    PartitionTask,
)
from services.db_accuracy.table_specs import load_table_specs, resolve_spec


class PartitionPlannerService:
    def __init__(self, db: Any):
        self.db = db
        self.direct_reader = DBAccuracyReaderService(db)
        self.cached_reader = CachedDBReaderService(db)

    def plan(self, request: PartitionedAccuracyRequest) -> list[PartitionTask]:
        _validate_request(request)
        specs = self._selected_specs(request.tables)

        tasks: list[PartitionTask] = []
        for spec in specs:
            if spec.kind == "registry":
                tasks.append(_registry_task(spec))
                continue

            resolved = self._resolve(spec)
            if resolved.time_field is None:
                raise ValueError(f"{spec.table} has no resolved time field")

            if request.start_ms is None or request.end_ms is None:
                tasks.extend(self._plan_discovered_ranges(resolved, request))
            else:
                tasks.extend(self._plan_explicit_range(resolved, request))
        return tasks

    def _selected_specs(self, tables: tuple[str, ...]) -> list[TableSpec]:
        specs_by_table = {spec.table: spec for spec in load_table_specs()}
        unknown = [table for table in tables if table not in specs_by_table]
        if unknown:
            raise ValueError(f"unknown DB accuracy table(s): {unknown}")
        return [specs_by_table[table] for table in tables]

    def _resolve(self, spec: TableSpec) -> ResolvedTableSpec:
        columns = self.direct_reader.table_columns(spec.table)
        return resolve_spec(spec, columns)

    def _plan_discovered_ranges(
        self,
        spec: ResolvedTableSpec,
        request: PartitionedAccuracyRequest,
    ) -> list[PartitionTask]:
        stable_before_ms = _stable_before_ms(request.safety_hours)
        ranges = self.direct_reader.key_ranges(spec, stable_before_ms)
        ranges = [
            time_range
            for time_range in ranges
            if _matches_filters(time_range.key.values, spec, request)
        ][: request.max_shards]

        tasks: list[PartitionTask] = []
        for time_range in ranges:
            tasks.extend(
                _tasks_for_range(
                    spec=spec,
                    key_values=time_range.key.values,
                    start_ms=time_range.start_ms,
                    end_ms=time_range.end_ms,
                    partition_days=request.partition_days,
                )
            )
        return tasks

    def _plan_explicit_range(
        self,
        spec: ResolvedTableSpec,
        request: PartitionedAccuracyRequest,
    ) -> list[PartitionTask]:
        if request.start_ms is None or request.end_ms is None:
            raise ValueError("explicit range planning requires start_ms and end_ms")
        if spec.time_field is None:
            raise ValueError(f"{spec.spec.table} has no resolved time field")

        key_values = self.cached_reader.discover_market_keys(
            table=spec.spec.table,
            key_fields=spec.key_fields,
            time_field=spec.time_field,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            filters=_discovery_filters(spec, request),
            limit=request.max_shards,
        )
        key_values = [
            values for values in key_values if _matches_filters(values, spec, request)
        ][: request.max_shards]

        tasks: list[PartitionTask] = []
        for values in key_values:
            tasks.extend(
                _tasks_for_range(
                    spec=spec,
                    key_values=values,
                    start_ms=request.start_ms,
                    end_ms=request.end_ms,
                    partition_days=request.partition_days,
                )
            )
        return tasks


def _validate_request(request: PartitionedAccuracyRequest) -> None:
    if request.mode == AccuracyMode.CACHED:
        if len(request.tables) != 1:
            raise ValueError("cached mode requires exactly one table")
        if request.start_ms is None or request.end_ms is None:
            raise ValueError("cached mode requires start_ms and end_ms")

    if request.partition_days < 1:
        raise ValueError("partition_days must be >= 1")
    if request.max_shards < 1:
        raise ValueError("max_shards must be >= 1")
    if request.start_ms is not None and request.end_ms is not None and request.end_ms < request.start_ms:
        raise ValueError("end_ms must be greater than or equal to start_ms")
    if (request.start_ms is None) != (request.end_ms is None):
        raise ValueError("start_ms and end_ms must be provided together")


def _registry_task(spec: TableSpec) -> PartitionTask:
    return PartitionTask(
        table=spec.table,
        kind=spec.kind,
        endpoint=spec.endpoint,
        key_values={},
        time_field=None,
        source_time_field=spec.source_time_field,
        compare_fields=spec.compare_fields,
        request_limit=spec.request_limit,
        start_ms=None,
        end_ms=None,
        partition_label="registry",
        partition_bucket="registry",
        is_registry=True,
        key_fields=spec.key_fields,
        interval_field=spec.interval_field,
        fixed_interval=spec.fixed_interval,
        symbol_field=spec.symbol_field,
        pair_field=spec.pair_field,
        contract_type_field=spec.contract_type_field,
    )


def _tasks_for_range(
    *,
    spec: ResolvedTableSpec,
    key_values: dict[str, Any],
    start_ms: int,
    end_ms: int,
    partition_days: int,
) -> list[PartitionTask]:
    if spec.time_field is None:
        raise ValueError(f"{spec.spec.table} has no resolved time field")

    return [
        PartitionTask(
            table=spec.spec.table,
            kind=spec.spec.kind,
            endpoint=spec.spec.endpoint,
            key_values=dict(key_values),
            time_field=spec.time_field,
            source_time_field=spec.spec.source_time_field or spec.time_field,
            compare_fields=spec.compare_fields,
            request_limit=spec.spec.request_limit,
            start_ms=partition.start_ms,
            end_ms=partition.end_ms,
            partition_label=partition.label,
            partition_bucket=partition.bucket,
            key_fields=spec.key_fields,
            interval_field=spec.interval_field,
            fixed_interval=spec.spec.fixed_interval,
            symbol_field=spec.spec.symbol_field,
            pair_field=spec.spec.pair_field,
            contract_type_field=spec.spec.contract_type_field,
        )
        for partition in split_time_partitions(start_ms, end_ms, partition_days)
    ]


def _matches_filters(
    key_values: dict[str, Any],
    spec: ResolvedTableSpec,
    request: PartitionedAccuracyRequest,
) -> bool:
    filters = _filter_sets(spec, request)
    return all(key_values.get(field) in values for field, values in filters.items())


def _discovery_filters(
    spec: ResolvedTableSpec,
    request: PartitionedAccuracyRequest,
) -> dict[str, Any]:
    return {
        field: next(iter(values))
        for field, values in _filter_sets(spec, request).items()
        if len(values) == 1
    }


def _filter_sets(
    spec: ResolvedTableSpec,
    request: PartitionedAccuracyRequest,
) -> dict[str, set[Any]]:
    field_values = (
        (spec.spec.symbol_field, request.symbols),
        (spec.spec.pair_field, request.pairs),
        (spec.spec.contract_type_field, request.contract_types),
        (spec.interval_field, request.intervals),
    )
    return {
        field: set(values)
        for field, values in field_values
        if field is not None and field in spec.key_fields and values
    }


def _stable_before_ms(safety_hours: int) -> int:
    stable_before = datetime.now(UTC) - timedelta(hours=safety_hours)
    return int(stable_before.timestamp() * 1000)
