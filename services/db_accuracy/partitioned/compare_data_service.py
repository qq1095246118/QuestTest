"""统一分区 DB/source 对比缓存服务。"""

from __future__ import annotations

import hashlib
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import polars as pl

from services.db_accuracy.cached.datacompy_service import DataComPyCompareService
from services.db_accuracy.partitioned.cache_store_service import (
    CompareCachePaths,
    PartitionedCacheStoreService,
)
from services.db_accuracy.partitioned.models import (
    SCHEMA_VERSION,
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    PartitionTask,
)


MAX_PATH_PART_LENGTH = 48


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
        paths = _bounded_compare_paths(self.store, task)
        cached_manifest = self.store.read_compare_manifest(paths)
        if cached_manifest is not None and cached_manifest.reusable_for(
            task,
            db_fingerprint,
            source_fingerprint,
        ):
            return cached_manifest

        tmp_dir = self.store.tmp_root / "compare" / uuid4().hex
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = DataComPyCompareService(report_root=tmp_dir).compare(
                shard_label=_short_label("shard", task.label),
                partition_label=_short_label("partition", task.partition_label),
                db_frame=db_frame,
                source_frame=source_frame,
                join_columns=task.join_columns,
            )
            generated_report_path = tmp_dir / result.report_path
            generated_diff_path = tmp_dir / result.diff_path
            report_text = generated_report_path.read_text(encoding="utf-8")
            diff_json = generated_diff_path.read_text(encoding="utf-8")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with suppress(OSError):
                tmp_dir.parent.rmdir()

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
        return manifest


def _bounded_compare_paths(
    store: PartitionedCacheStoreService,
    task: PartitionTask,
) -> CompareCachePaths:
    try:
        paths = store.compare_paths(task)
        paths.manifest_path.exists()
        return paths
    except OSError:
        directory = store.root / "compare" / _short_label("task", task.label)
        return CompareCachePaths(
            report_path=directory / "report.txt",
            diff_path=directory / "diff.json",
            manifest_path=directory / "manifest.json",
        )


def _short_label(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    safe = []
    for char in text:
        if char.isalnum() or char in {"-", "_", "="}:
            safe.append(char)
        else:
            safe.append("_")
    normalized = "".join(safe).strip("_") or prefix
    max_prefix_length = max(1, MAX_PATH_PART_LENGTH - len(prefix) - len(digest) - 2)
    return f"{prefix}-{normalized[:max_prefix_length]}-{digest}"
