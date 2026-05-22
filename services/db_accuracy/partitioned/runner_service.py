"""统一分区 DB accuracy runner。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import json
from typing import Any, Iterable

from services.db_accuracy.partitioned.aggregation_service import PartitionedAggregationService
from services.db_accuracy.partitioned.cache_store_service import (
    CompareCachePaths,
    PartitionedCacheStoreService,
)
from services.db_accuracy.partitioned.compare_data_service import PartitionedCompareDataService
from services.db_accuracy.partitioned.db_data_service import PartitionedDBDataService
from services.db_accuracy.partitioned.models import (
    CacheManifest,
    CacheSide,
    CompareManifest,
    PartitionTask,
    PartitionedAccuracyRequest,
    PartitionedRunResult,
    RunPauseReason,
)
from services.db_accuracy.partitioned.planner_service import PartitionPlannerService
from services.db_accuracy.partitioned.source_data_service import (
    PartitionedSourceDataService,
    SourceRequestFailed,
)


class PartitionedAccuracyService:
    def __init__(self, db: Any = None, source: Any = None) -> None:
        if db is None:
            from infrastructure.database.db_client import DBClient

            db = DBClient()
        self.db = db
        self.source = source

    def run(self, request: PartitionedAccuracyRequest) -> PartitionedRunResult:
        store = PartitionedCacheStoreService(request.cache_root)
        store.cleanup_tmp()
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        tasks: list[PartitionTask] = []
        db_manifests: dict[str, CacheManifest] = {}
        source_manifests: dict[str, CacheManifest] = {}
        pause_reason: RunPauseReason | None = None
        compare_manifests: list[CompareManifest] = []

        try:
            tasks = PartitionPlannerService(self.db).plan(request)
            db_service = PartitionedDBDataService(self.db, store)
            source_service = PartitionedSourceDataService(store, self.source)
            compare_service = PartitionedCompareDataService(store)

            db_manifests = self._prepare_db(tasks, db_service, request)
            source_manifests, pause_reason = self._prepare_source(
                tasks,
                source_service,
                request,
            )
            if pause_reason is None:
                compare_manifests = self._compare_all(
                    tasks=tasks,
                    db_manifests=db_manifests,
                    source_manifests=source_manifests,
                    store=store,
                    compare_service=compare_service,
                    request=request,
                )
        finally:
            store.cleanup_tmp()

        manifests = _dedupe_compare_manifests(
            (
                *_historical_complete_manifests(
                    store,
                    tasks,
                    db_manifests,
                    source_manifests,
                ),
                *compare_manifests,
            )
        )
        return PartitionedAggregationService(request.cache_root).aggregate(
            run_id=run_id,
            tasks=tasks,
            manifests=manifests,
            pause_reason=pause_reason,
        )

    def _prepare_db(
        self,
        tasks: list[PartitionTask],
        db_service: PartitionedDBDataService,
        request: PartitionedAccuracyRequest,
    ) -> dict[str, CacheManifest]:
        manifests: dict[str, CacheManifest] = {}
        with ThreadPoolExecutor(max_workers=_workers(request)) as executor:
            futures: dict[Future[tuple[Any, CacheManifest]], PartitionTask] = {
                executor.submit(
                    db_service.ensure_db_frame,
                    task,
                    request.cache_policy,
                ): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                _, manifest = future.result()
                manifests[task.label] = manifest
        return manifests

    def _prepare_source(
        self,
        tasks: list[PartitionTask],
        source_service: PartitionedSourceDataService,
        request: PartitionedAccuracyRequest,
    ) -> tuple[dict[str, CacheManifest], RunPauseReason | None]:
        manifests: dict[str, CacheManifest] = {}
        with ThreadPoolExecutor(max_workers=_workers(request)) as executor:
            futures: dict[Future[tuple[Any, CacheManifest]], PartitionTask] = {
                executor.submit(
                    source_service.ensure_source_frame,
                    task,
                    request.cache_policy,
                    request.execution,
                ): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    _, manifest = future.result()
                except SourceRequestFailed as exc:
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    failed_task = exc.task or task
                    return manifests, RunPauseReason(
                        reason="source_request_failed",
                        task_label=getattr(failed_task, "label", task.label),
                        message=str(exc),
                    )
                manifests[task.label] = manifest
        return manifests, None

    def _compare_all(
        self,
        *,
        tasks: list[PartitionTask],
        db_manifests: dict[str, CacheManifest],
        source_manifests: dict[str, CacheManifest],
        store: PartitionedCacheStoreService,
        compare_service: PartitionedCompareDataService,
        request: PartitionedAccuracyRequest,
    ) -> list[CompareManifest]:
        manifests: list[CompareManifest] = []
        with ThreadPoolExecutor(max_workers=_workers(request)) as executor:
            futures: dict[Future[CompareManifest], PartitionTask] = {}
            for task in tasks:
                db_manifest = db_manifests[task.label]
                source_manifest = source_manifests[task.label]
                db_hit = store.find_covering_data_cache(CacheSide.DB, task)
                source_hit = store.find_covering_data_cache(CacheSide.SOURCE, task)
                if db_hit is None or source_hit is None:
                    raise RuntimeError(f"prepared data cache disappeared for {task.label}")
                db_frame = store.read_data_frame(db_hit.paths, task, task.time_field)
                source_frame = store.read_data_frame(
                    source_hit.paths,
                    task,
                    task.time_field,
                )
                futures[
                    executor.submit(
                        compare_service.ensure_compare,
                        task,
                        db_frame,
                        source_frame,
                        db_manifest.fingerprint,
                        source_manifest.fingerprint,
                    )
                ] = task
            for future in as_completed(futures):
                manifests.append(future.result())
        return manifests


def _workers(request: PartitionedAccuracyRequest) -> int:
    return max(1, int(request.execution.workers))


def _historical_complete_manifests(
    store: PartitionedCacheStoreService,
    tasks: list[PartitionTask],
    db_manifests: dict[str, CacheManifest],
    source_manifests: dict[str, CacheManifest],
) -> list[CompareManifest]:
    tasks_by_key = {_task_key(task): task for task in tasks}
    manifests: list[CompareManifest] = []
    compare_root = store.root / "compare"
    if not compare_root.exists():
        return manifests

    for manifest_path in sorted(compare_root.glob("**/manifest.json")):
        paths = CompareCachePaths(
            report_path=manifest_path.parent / "report.txt",
            diff_path=manifest_path.parent / "diff.json",
            manifest_path=manifest_path,
        )
        manifest = store.read_compare_manifest(paths)
        if manifest is None or not manifest.complete:
            continue
        task = tasks_by_key.get(_manifest_key(manifest))
        if task is None:
            continue
        db_manifest = db_manifests.get(task.label)
        source_manifest = source_manifests.get(task.label)
        if db_manifest is None or source_manifest is None:
            continue
        if manifest.reusable_for(
            task,
            db_manifest.fingerprint,
            source_manifest.fingerprint,
        ):
            manifests.append(manifest)
    return manifests


def _dedupe_compare_manifests(
    manifests: Iterable[CompareManifest],
) -> list[CompareManifest]:
    by_key: dict[tuple[Any, ...], CompareManifest] = {}
    for manifest in manifests:
        by_key[_manifest_key(manifest)] = manifest
    return list(by_key.values())


def _task_key(task: PartitionTask) -> tuple[Any, ...]:
    return (
        task.table,
        task.endpoint,
        _market_key_items(task.key_values),
        task.start_ms,
        task.end_ms,
    )


def _manifest_key(manifest: CompareManifest) -> tuple[Any, ...]:
    return (
        manifest.table,
        manifest.endpoint,
        _market_key_items(manifest.market_key),
        manifest.start_ms,
        manifest.end_ms,
    )


def _market_key_items(market_key: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(key), json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
            for key, value in market_key.items()
        )
    )
