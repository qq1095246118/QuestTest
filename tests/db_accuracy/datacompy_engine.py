from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from datacompy.polars import PolarsCompare

from tests.db_accuracy.cache_models import CachedShardResult


SAMPLE_LIMIT = 20
SOURCE_SUFFIX = "__source"


@dataclass(frozen=True)
class CompareSummary:
    db_only_count: int
    source_only_count: int
    unequal_count: int

    @property
    def differences(self) -> int:
        return self.db_only_count + self.source_only_count + self.unequal_count


class DataComPyEngine:
    def __init__(self, report_root: Path):
        self.report_root = Path(report_root)

    def compare(
        self,
        shard_label: str,
        partition_label: str,
        db_frame: pl.DataFrame,
        source_frame: pl.DataFrame,
        join_columns: tuple[str, ...],
    ) -> CachedShardResult:
        self.report_root.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_name(f"{shard_label},{partition_label}")
        report_path = self.report_root / f"{safe_name}.report.txt"
        diff_path = self.report_root / f"{safe_name}.diff.json"

        compare_columns = _common_compare_columns(db_frame, source_frame, join_columns)
        datacompy_columns = [*join_columns, *compare_columns]
        db_compare_frame = _with_missing_columns(
            db_frame,
            datacompy_columns,
            source_frame,
        )
        source_compare_frame = _with_missing_columns(
            source_frame,
            datacompy_columns,
            db_frame,
        )
        compare = PolarsCompare(
            db_compare_frame.select(datacompy_columns),
            source_compare_frame.select(datacompy_columns),
            join_columns=list(join_columns),
            abs_tol=0,
            rel_tol=0,
            df1_name="db",
            df2_name="binance",
            cast_column_names_lower=False,
        )
        report_path.write_text(compare.report(), encoding="utf-8")

        summary = _build_summary(
            db_compare_frame,
            source_compare_frame,
            join_columns,
            compare_columns,
        )
        diff_payload = _build_diff_payload(
            db_compare_frame,
            source_compare_frame,
            join_columns,
            compare_columns,
            summary,
        )
        diff_path.write_text(
            json.dumps(diff_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        status = "passed" if summary.differences == 0 else "failed"
        return CachedShardResult(
            shard_label=shard_label,
            partition_label=partition_label,
            status=status,
            db_rows=db_frame.height,
            source_rows=source_frame.height,
            differences=summary.differences,
            report_path=str(report_path.relative_to(self.report_root)),
            diff_path=str(diff_path.relative_to(self.report_root)),
            message=None if status == "passed" else "cached comparison found differences",
        )


def _build_summary(
    db_frame: pl.DataFrame,
    source_frame: pl.DataFrame,
    join_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
) -> CompareSummary:
    return CompareSummary(
        db_only_count=_anti_join(db_frame, source_frame, join_columns).height,
        source_only_count=_anti_join(source_frame, db_frame, join_columns).height,
        unequal_count=_unequal_joined_rows(
            db_frame,
            source_frame,
            join_columns,
            compare_columns,
        ).height,
    )


def _build_diff_payload(
    db_frame: pl.DataFrame,
    source_frame: pl.DataFrame,
    join_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
    summary: CompareSummary,
) -> dict[str, Any]:
    unequal_rows = _unequal_joined_rows(
        db_frame,
        source_frame,
        join_columns,
        compare_columns,
    ).head(SAMPLE_LIMIT)
    return {
        "db_only_count": summary.db_only_count,
        "source_only_count": summary.source_only_count,
        "unequal_count": summary.unequal_count,
        "db_only_sample": _anti_join(db_frame, source_frame, join_columns)
        .head(SAMPLE_LIMIT)
        .to_dicts(),
        "source_only_sample": _anti_join(source_frame, db_frame, join_columns)
        .head(SAMPLE_LIMIT)
        .to_dicts(),
        "unequal_sample": _unequal_sample(
            unequal_rows,
            join_columns,
            compare_columns,
        ),
    }


def _anti_join(
    left: pl.DataFrame,
    right: pl.DataFrame,
    join_columns: tuple[str, ...],
) -> pl.DataFrame:
    if left.is_empty():
        return left
    if right.is_empty():
        return left
    return left.join(right.select(list(join_columns)), on=list(join_columns), how="anti")


def _unequal_joined_rows(
    db_frame: pl.DataFrame,
    source_frame: pl.DataFrame,
    join_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
) -> pl.DataFrame:
    if db_frame.is_empty() or source_frame.is_empty() or not compare_columns:
        return _empty_joined_frame(db_frame, source_frame, join_columns, compare_columns)

    joined = db_frame.select([*join_columns, *compare_columns]).join(
        source_frame.select([*join_columns, *compare_columns]),
        on=list(join_columns),
        how="inner",
        suffix=SOURCE_SUFFIX,
    )
    if joined.is_empty():
        return joined

    expressions = [
        pl.col(column).ne_missing(pl.col(f"{column}{SOURCE_SUFFIX}"))
        for column in compare_columns
    ]
    return joined.filter(pl.any_horizontal(expressions))


def _empty_joined_frame(
    db_frame: pl.DataFrame,
    source_frame: pl.DataFrame,
    join_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
) -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {}
    for column in join_columns:
        schema[column] = db_frame.schema.get(
            column,
            source_frame.schema.get(column, pl.String),
        )
    for column in compare_columns:
        schema[column] = db_frame.schema.get(column, pl.String)
        schema[f"{column}{SOURCE_SUFFIX}"] = source_frame.schema.get(column, pl.String)
    return pl.DataFrame(schema=schema)


def _unequal_sample(
    unequal_rows: pl.DataFrame,
    join_columns: tuple[str, ...],
    compare_columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in unequal_rows.to_dicts():
        changed_columns = [
            column
            for column in compare_columns
            if row.get(column) != row.get(f"{column}{SOURCE_SUFFIX}")
        ]
        samples.append(
            {
                "join_key": {column: row.get(column) for column in join_columns},
                "columns": changed_columns,
                "db": {column: row.get(column) for column in changed_columns},
                "source": {
                    column: row.get(f"{column}{SOURCE_SUFFIX}")
                    for column in changed_columns
                },
            }
        )
    return samples


def _common_compare_columns(
    db_frame: pl.DataFrame,
    source_frame: pl.DataFrame,
    join_columns: tuple[str, ...],
) -> tuple[str, ...]:
    join_column_set = set(join_columns)
    source_column_set = set(source_frame.columns)
    return tuple(
        column
        for column in db_frame.columns
        if column not in join_column_set and column in source_column_set
    )


def _with_missing_columns(
    frame: pl.DataFrame,
    columns: list[str],
    reference_frame: pl.DataFrame,
) -> pl.DataFrame:
    output = frame
    for column in columns:
        if column in output.columns:
            continue
        dtype = reference_frame.schema.get(column, pl.String)
        output = output.with_columns(pl.Series(column, [None] * output.height, dtype=dtype))
    return output


def _safe_name(text: str) -> str:
    safe = []
    for char in text:
        if char.isalnum() or char in {"-", "_", "="}:
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe)
