"""统一分区 DB accuracy 运行结果聚合服务。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from services.db_accuracy.partitioned.cache_store_service import PartitionedCacheStoreService
from services.db_accuracy.partitioned.models import (
    CompareManifest,
    PartitionedRunResult,
    PartitionTask,
    RunPauseReason,
    RunStatus,
)


class PartitionedAggregationService:
    def __init__(self, cache_root: Path):
        self.store = PartitionedCacheStoreService(cache_root)

    def aggregate(
        self,
        run_id: str,
        tasks: list[PartitionTask],
        manifests: Iterable[CompareManifest | None],
        pause_reason: RunPauseReason | None,
    ) -> PartitionedRunResult:
        complete_manifests = _complete_manifests_for_tasks(tasks, manifests)
        tasks_total = len(tasks)
        tasks_compared = len(complete_manifests)
        tasks_with_differences = sum(
            1
            for manifest in complete_manifests
            if manifest.differences > 0
        )
        db_rows = sum(manifest.db_rows for manifest in complete_manifests)
        source_rows = sum(manifest.source_rows for manifest in complete_manifests)
        differences = sum(manifest.differences for manifest in complete_manifests)
        status = _run_status(
            pause_reason=pause_reason,
            tasks_total=tasks_total,
            tasks_compared=tasks_compared,
            differences=differences,
        )
        failure_reason = _failure_reason(
            status=status,
            tasks_total=tasks_total,
            tasks_compared=tasks_compared,
        )
        details = {
            "run_id": run_id,
            "status": status.value,
            "tasks_total": tasks_total,
            "tasks_compared": tasks_compared,
            "tasks_with_differences": tasks_with_differences,
            "db_rows": db_rows,
            "source_rows": source_rows,
            "differences": differences,
            "failure_reason": failure_reason,
            "pause_reason": _pause_reason_payload(pause_reason),
            "partitions": [_partition_payload(manifest) for manifest in complete_manifests],
        }
        summary_text = _summary_text(
            status=status,
            tasks_total=tasks_total,
            tasks_compared=tasks_compared,
            tasks_with_differences=tasks_with_differences,
            db_rows=db_rows,
            source_rows=source_rows,
            differences=differences,
            failure_reason=failure_reason,
            pause_reason=pause_reason,
        )
        result = PartitionedRunResult(
            status=status,
            tasks_total=tasks_total,
            tasks_compared=tasks_compared,
            tasks_with_differences=tasks_with_differences,
            db_rows=db_rows,
            source_rows=source_rows,
            differences=differences,
            summary_text=summary_text,
            details=details,
            pause_reason=pause_reason,
        )
        self._write_run_artifacts(run_id, result)
        return result

    def _write_run_artifacts(self, run_id: str, result: PartitionedRunResult) -> None:
        run_root = self.store.run_root(run_id)
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "summary.json").write_text(
            json.dumps(result.details, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (run_root / "summary.txt").write_text(result.summary_text, encoding="utf-8")


def _run_status(
    *,
    pause_reason: RunPauseReason | None,
    tasks_total: int,
    tasks_compared: int,
    differences: int,
) -> RunStatus:
    if pause_reason is not None:
        return RunStatus.PAUSED
    if tasks_total == 0:
        return RunStatus.FAILED
    if tasks_compared < tasks_total:
        return RunStatus.FAILED
    if differences > 0:
        return RunStatus.COMPLETED_WITH_DIFFERENCES
    return RunStatus.PASSED


def _failure_reason(
    *,
    status: RunStatus,
    tasks_total: int,
    tasks_compared: int,
) -> str | None:
    if status != RunStatus.FAILED:
        return None
    if tasks_total == 0:
        return "no_tasks_planned"
    if tasks_compared < tasks_total:
        return "incomplete_comparison"
    return "run_failed"


def _complete_manifests_for_tasks(
    tasks: list[PartitionTask],
    manifests: Iterable[CompareManifest | None],
) -> list[CompareManifest]:
    task_keys = {_task_key(task) for task in tasks}
    manifests_by_key: dict[tuple[Any, ...], CompareManifest] = {}
    for manifest in manifests:
        if manifest is None or not manifest.complete:
            continue
        key = _manifest_key(manifest)
        if key not in task_keys:
            continue
        if key in manifests_by_key:
            del manifests_by_key[key]
        manifests_by_key[key] = manifest
    return list(manifests_by_key.values())


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


def _summary_text(
    *,
    status: RunStatus,
    tasks_total: int,
    tasks_compared: int,
    tasks_with_differences: int,
    db_rows: int,
    source_rows: int,
    differences: int,
    failure_reason: str | None,
    pause_reason: RunPauseReason | None,
) -> str:
    lines = [
        f"status={status.value}",
        f"tasks_total={tasks_total}",
        f"tasks_compared={tasks_compared}",
        f"tasks_with_differences={tasks_with_differences}",
        f"db_rows={db_rows}",
        f"source_rows={source_rows}",
        f"differences={differences}",
    ]
    if failure_reason is not None:
        lines.append(f"failure_reason={failure_reason}")
    if pause_reason is not None:
        lines.extend(
            [
                f"pause_reason={pause_reason.reason}",
                f"pause_task={pause_reason.task_label}",
                f"pause_message={pause_reason.message}",
            ]
        )
    return "\n".join(lines)


def _pause_reason_payload(pause_reason: RunPauseReason | None) -> dict[str, str] | None:
    if pause_reason is None:
        return None
    return {
        "reason": pause_reason.reason,
        "task": pause_reason.task_label,
        "message": pause_reason.message,
    }


def _partition_payload(manifest: CompareManifest) -> dict[str, object]:
    return {
        "table": manifest.table,
        "endpoint": manifest.endpoint,
        "market_key": dict(manifest.market_key),
        "start_ms": manifest.start_ms,
        "end_ms": manifest.end_ms,
        "status": manifest.status.value,
        "db_rows": manifest.db_rows,
        "source_rows": manifest.source_rows,
        "differences": manifest.differences,
        "report_path": manifest.report_path,
        "diff_path": manifest.diff_path,
        "message": manifest.message,
    }
