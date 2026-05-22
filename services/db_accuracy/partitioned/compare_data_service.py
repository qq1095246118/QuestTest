"""统一分区 DB/source 对比缓存服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from services.db_accuracy.cached.datacompy_service import DataComPyCompareService
from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import (
    SCHEMA_VERSION,
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    PartitionTask,
)


class PartitionedCompareDataService:
    def __init__(self, store: PartitionedCacheStoreService):
        self.store = store

    def ensure_compare(
        self,
        task: PartitionTask,
        db_frame: pl.DataFrame,
        source_frame: pl.DataFrame,
        db_fingerprint: DataFingerprint | None,
        source_fingerprint: DataFingerprint | None,
    ) -> CompareManifest:
        paths = self.store.compare_paths(task)
        cached_manifest = self.store.read_compare_manifest(paths)
        if cached_manifest is not None and cached_manifest.reusable_for(
            task,
            db_fingerprint,
            source_fingerprint,
        ):
            return cached_manifest

        result = DataComPyCompareService(report_root=paths.report_path.parent).compare(
            shard_label=task.label,
            partition_label=task.partition_label,
            db_frame=db_frame,
            source_frame=source_frame,
            join_columns=task.join_columns,
        )
        generated_report_path = paths.report_path.parent / result.report_path
        generated_diff_path = paths.diff_path.parent / result.diff_path
        report_text = generated_report_path.read_text(encoding="utf-8")
        diff_json = generated_diff_path.read_text(encoding="utf-8")

        manifest = CompareManifest(
            schema_version=SCHEMA_VERSION,
            table=task.table,
            endpoint=task.endpoint,
            market_key=dict(task.key_values),
            start_ms=task.start_ms,
            end_ms=task.end_ms,
            status=(
                CompareStatus.PASSED
                if result.differences == 0
                else CompareStatus.FAILED_WITH_DIFFERENCES
            ),
            db_fingerprint=db_fingerprint,
            source_fingerprint=source_fingerprint,
            db_rows=result.db_rows,
            source_rows=result.source_rows,
            differences=result.differences,
            report_path=self.store.relative_to_root(paths.report_path),
            diff_path=self.store.relative_to_root(paths.diff_path),
            message=result.message,
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        self.store.write_compare_artifacts(paths, report_text, diff_json, manifest)
        _unlink_generated(generated_report_path, paths.report_path)
        _unlink_generated(generated_diff_path, paths.diff_path)
        return manifest


def _unlink_generated(generated_path: Path, fixed_path: Path) -> None:
    if generated_path != fixed_path and generated_path.exists():
        generated_path.unlink()
