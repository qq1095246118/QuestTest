"""统一分区缓存读写服务。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import polars as pl

from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CacheStatus,
    CompareManifest,
    DataFingerprint,
    PartitionTask,
)


@dataclass(frozen=True)
class DataCachePaths:
    data_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class CompareCachePaths:
    report_path: Path
    diff_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class DataCacheHit:
    paths: DataCachePaths
    manifest: CacheManifest


class PartitionedCacheStoreService:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.tmp_root = self.root / "_tmp"

    def data_paths(self, side: CacheSide, task: PartitionTask) -> DataCachePaths:
        directory = self.root / side.value
        for part in task.path_parts:
            directory = directory / part
        return DataCachePaths(
            data_path=directory / "data.parquet",
            manifest_path=directory / "manifest.json",
        )

    def compare_paths(self, task: PartitionTask) -> CompareCachePaths:
        directory = self.root / "compare"
        for part in task.path_parts:
            directory = directory / part
        return CompareCachePaths(
            report_path=directory / "report.txt",
            diff_path=directory / "diff.json",
            manifest_path=directory / "manifest.json",
        )

    def run_root(self, run_id: str) -> Path:
        return self.root / "runs" / f"run_id={run_id}"

    def read_data_manifest(self, paths: DataCachePaths) -> CacheManifest | None:
        if not paths.manifest_path.exists():
            return None
        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
            return CacheManifest.from_dict(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def read_compare_manifest(self, paths: CompareCachePaths) -> CompareManifest | None:
        if not paths.manifest_path.exists():
            return None
        if not paths.report_path.exists() or not paths.diff_path.exists():
            return None
        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
            return CompareManifest.from_dict(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def find_covering_data_cache(self, side: CacheSide, task: PartitionTask) -> DataCacheHit | None:
        exact_paths = self.data_paths(side, task)
        exact_manifest = self.read_data_manifest(exact_paths)
        if exact_manifest is not None and self._data_cache_is_reusable(
            side,
            task,
            exact_paths,
            exact_manifest,
        ):
            return DataCacheHit(paths=exact_paths, manifest=exact_manifest)

        base = self.root / side.value
        if not base.exists():
            return None
        for manifest_path in sorted(base.glob("**/manifest.json")):
            paths = DataCachePaths(
                data_path=manifest_path.parent / "data.parquet",
                manifest_path=manifest_path,
            )
            manifest = self.read_data_manifest(paths)
            if manifest is None or not self._data_cache_is_reusable(side, task, paths, manifest):
                continue
            return DataCacheHit(paths=paths, manifest=manifest)
        return None

    def read_data_frame(
        self,
        paths: DataCachePaths,
        task: PartitionTask,
        time_field: str | None,
    ) -> pl.DataFrame:
        manifest = self.read_data_manifest(paths)
        if manifest is not None and manifest.status == CacheStatus.EMPTY:
            return pl.DataFrame()
        if not paths.data_path.exists():
            return pl.DataFrame()
        frame = pl.read_parquet(paths.data_path)
        if task.is_registry or time_field is None or frame.is_empty():
            return frame
        if time_field not in frame.columns:
            raise ValueError(f"Time field {time_field!r} is missing from cached frame")
        if task.start_ms is None or task.end_ms is None:
            return frame
        time_expr = pl.col(time_field).cast(pl.Int64)
        return frame.filter(
            time_expr >= int(task.start_ms),
            time_expr <= int(task.end_ms),
        )

    def write_data_frame(
        self,
        paths: DataCachePaths,
        frame: pl.DataFrame,
        manifest: CacheManifest,
    ) -> None:
        paths.data_path.parent.mkdir(parents=True, exist_ok=True)
        if manifest.status in {CacheStatus.FAILED, CacheStatus.CANCELLED}:
            return
        tmp_dir = self._tmp_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_data = tmp_dir / "data.parquet"
        tmp_manifest = tmp_dir / "manifest.json"
        try:
            if manifest.status == CacheStatus.COMPLETE:
                frame.write_parquet(tmp_data)
                tmp_data.replace(paths.data_path)
            elif manifest.status == CacheStatus.EMPTY and paths.data_path.exists():
                paths.data_path.unlink()
            tmp_manifest.write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_manifest.replace(paths.manifest_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def write_compare_artifacts(
        self,
        paths: CompareCachePaths,
        report_text: str,
        diff_json: str,
        manifest: CompareManifest,
    ) -> None:
        paths.report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self._tmp_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_report = tmp_dir / "report.txt"
        tmp_diff = tmp_dir / "diff.json"
        tmp_manifest = tmp_dir / "manifest.json"
        try:
            tmp_report.write_text(report_text, encoding="utf-8")
            tmp_diff.write_text(diff_json, encoding="utf-8")
            tmp_manifest.write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_report.replace(paths.report_path)
            tmp_diff.replace(paths.diff_path)
            tmp_manifest.replace(paths.manifest_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def clear_data_cache(self, side: CacheSide, task: PartitionTask) -> None:
        paths = self.data_paths(side, task)
        for path in (paths.data_path, paths.manifest_path):
            if path.exists():
                path.unlink()

    def cleanup_tmp(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def relative_to_root(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return str(path.relative_to(self.root))

    def _tmp_dir(self) -> Path:
        return self.tmp_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") / uuid4().hex

    def _data_cache_is_reusable(
        self,
        side: CacheSide,
        task: PartitionTask,
        paths: DataCachePaths,
        manifest: CacheManifest,
    ) -> bool:
        if manifest.side != side:
            return False
        if not manifest.reusable_for(task):
            return False
        if manifest.status == CacheStatus.EMPTY:
            return True
        if not paths.data_path.exists():
            return False
        if manifest.fingerprint is None:
            return False
        return fingerprint_frame(pl.read_parquet(paths.data_path)) == manifest.fingerprint


def fingerprint_frame(frame: pl.DataFrame) -> DataFingerprint:
    payload = frame.write_json()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return DataFingerprint(row_count=frame.height, content_hash=digest)
