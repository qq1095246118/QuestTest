"""cached 模式本地缓存读写服务。

本模块负责 cached 模式源数据 Parquet 与 manifest 文件的路径管理和读写。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from services.db_accuracy.cached.cache_models import CacheManifest, MarketShard, TimePartition


@dataclass(frozen=True)
class CachePaths:
    data_path: Path
    manifest_path: Path


class CacheStoreService:
    def __init__(self, root: Path):
        self.root = Path(root)

    def paths_for(self, shard: MarketShard, partition: TimePartition) -> CachePaths:
        directory = self.root / "source"
        for part in shard.path_parts:
            directory = directory / part
        directory = directory / partition.bucket
        return CachePaths(
            data_path=directory / "data.parquet",
            manifest_path=directory / "manifest.json",
        )

    def read_manifest(self, paths: CachePaths) -> CacheManifest | None:
        if not paths.manifest_path.exists():
            return None
        payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        return CacheManifest(**payload)

    def write_manifest(self, paths: CachePaths, manifest: CacheManifest) -> None:
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if manifest.status != "complete" and paths.data_path.exists():
            paths.data_path.unlink()
        paths.manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def has_complete_partition(self, paths: CachePaths) -> bool:
        manifest = self.read_manifest(paths)
        if manifest is None:
            return False
        if manifest.status not in {"complete", "empty", "source_market_unavailable"}:
            return False
        if manifest.status == "complete" and not paths.data_path.exists():
            return False
        return True

    def read_frame(self, paths: CachePaths) -> pl.DataFrame:
        if not paths.data_path.exists():
            return pl.DataFrame()
        return pl.read_parquet(paths.data_path)

    def write_frame(self, paths: CachePaths, frame: pl.DataFrame) -> None:
        paths.data_path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(paths.data_path)
