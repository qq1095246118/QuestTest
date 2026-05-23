from __future__ import annotations

import json
from pathlib import Path

from services.db_accuracy.partitioned.aggregation_service import PartitionedAggregationService
from services.db_accuracy.partitioned.models import (
    CompareManifest,
    CompareStatus,
    DataFingerprint,
    RunPauseReason,
    RunStatus,
    PartitionTask,
)


def _task(
    start_ms: int = 1704067200000,
    end_ms: int = 1704153599999,
    symbol: str = "BTCUSDT",
    key_values: dict[str, str] | None = None,
) -> PartitionTask:
    if key_values is None:
        key_values = {"symbol": symbol, "interval": "1m"}
    return PartitionTask(
        table="binance_kline_all_future_raw",
        kind="kline",
        endpoint="usdm_klines",
        key_values=key_values,
        time_field="timestamp",
        source_time_field="timestamp",
        compare_fields=("timestamp", "open", "close"),
        request_limit=1000,
        start_ms=start_ms,
        end_ms=end_ms,
        partition_label=f"{start_ms}-{end_ms}",
        partition_bucket="date=2024-01-01",
        is_registry=False,
    )


def _manifest(task: PartitionTask, differences: int = 0) -> CompareManifest:
    return CompareManifest(
        schema_version=1,
        table=task.table,
        endpoint=task.endpoint,
        market_key=dict(task.key_values),
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        status=(
            CompareStatus.PASSED
            if differences == 0
            else CompareStatus.FAILED_WITH_DIFFERENCES
        ),
        db_fingerprint=DataFingerprint(row_count=2, content_hash=f"db-{task.label}"),
        source_fingerprint=DataFingerprint(row_count=2, content_hash=f"source-{task.label}"),
        db_rows=2,
        source_rows=2,
        differences=differences,
        report_path=f"compare/{task.partition_label}/report.txt",
        diff_path=f"compare/{task.partition_label}/diff.json",
        message=None if differences == 0 else "cached comparison found differences",
        created_at_utc="2026-05-22T00:00:00+00:00",
    )


def test_aggregate_completed_with_differences_and_writes_run_artifacts(tmp_path: Path) -> None:
    tasks = [_task(symbol="BTCUSDT"), _task(symbol="ETHUSDT")]
    manifests = [_manifest(tasks[0], differences=0), _manifest(tasks[1], differences=3)]
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-1",
        tasks=tasks,
        manifests=manifests,
        pause_reason=None,
    )

    run_root = tmp_path / "runs" / "run_id=run-1"
    assert result.status == RunStatus.COMPLETED_WITH_DIFFERENCES
    assert result.tasks_total == 2
    assert result.tasks_compared == 2
    assert result.tasks_with_differences == 1
    assert result.db_rows == 4
    assert result.source_rows == 4
    assert result.differences == 3
    assert (run_root / "summary.json").exists()
    assert (run_root / "summary.txt").exists()
    assert "status=completed_with_differences" in (run_root / "summary.txt").read_text(
        encoding="utf-8"
    )
    payload = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
    assert payload["partitions"][1]["differences"] == 3


def test_aggregate_paused_includes_pause_reason_in_summary_and_details(tmp_path: Path) -> None:
    task = _task()
    pause_reason = RunPauseReason(
        reason="source_failed",
        task_label=task.label,
        message="HTTP 429",
    )
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-2",
        tasks=[task],
        manifests=[],
        pause_reason=pause_reason,
    )

    assert result.status == RunStatus.PAUSED
    assert result.pause_reason == pause_reason
    assert result.details["pause_reason"] == {
        "reason": "source_failed",
        "task": task.label,
        "message": "HTTP 429",
    }
    assert "pause_reason=source_failed" in result.summary_text
    assert f"pause_task={task.label}" in result.summary_text
    assert "pause_message=HTTP 429" in result.summary_text


def test_aggregate_failed_when_not_all_tasks_compared_without_pause(tmp_path: Path) -> None:
    tasks = [_task(symbol="BTCUSDT"), _task(symbol="ETHUSDT")]
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-3",
        tasks=tasks,
        manifests=[_manifest(tasks[0], differences=0)],
        pause_reason=None,
    )

    assert result.status == RunStatus.FAILED
    assert result.tasks_total == 2
    assert result.tasks_compared == 1
    assert result.tasks_with_differences == 0


def test_aggregate_failed_when_no_tasks_planned(tmp_path: Path) -> None:
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-empty",
        tasks=[],
        manifests=[],
        pause_reason=None,
    )

    assert result.status == RunStatus.FAILED
    assert result.tasks_total == 0
    assert result.tasks_compared == 0
    assert result.details["failure_reason"] == "no_tasks_planned"
    assert "failure_reason=no_tasks_planned" in result.summary_text


def test_aggregate_deduplicates_complete_manifests_by_task_identity(tmp_path: Path) -> None:
    task = _task()
    older_manifest = _manifest(task, differences=0)
    latest_manifest = _manifest(task, differences=2)
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-4",
        tasks=[task],
        manifests=[older_manifest, latest_manifest],
        pause_reason=None,
    )

    assert result.status == RunStatus.COMPLETED_WITH_DIFFERENCES
    assert result.tasks_total == 1
    assert result.tasks_compared == 1
    assert result.tasks_with_differences == 1
    assert result.db_rows == 2
    assert result.source_rows == 2
    assert result.differences == 2
    assert len(result.details["partitions"]) == 1
    assert result.details["partitions"][0]["differences"] == 2


def test_aggregate_ignores_foreign_complete_manifests(tmp_path: Path) -> None:
    task = _task(symbol="BTCUSDT")
    foreign_task = _task(symbol="ETHUSDT")
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-5",
        tasks=[task],
        manifests=[_manifest(task), _manifest(foreign_task, differences=4)],
        pause_reason=None,
    )

    assert result.status == RunStatus.PASSED
    assert result.tasks_total == 1
    assert result.tasks_compared == 1
    assert result.tasks_with_differences == 0
    assert result.differences == 0
    assert result.details["partitions"][0]["market_key"] == {
        "symbol": "BTCUSDT",
        "interval": "1m",
    }


def test_aggregate_missing_current_task_is_failed_even_with_foreign_complete_manifest(
    tmp_path: Path,
) -> None:
    task = _task(symbol="BTCUSDT")
    foreign_task = _task(symbol="ETHUSDT")
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-6",
        tasks=[task],
        manifests=[_manifest(foreign_task)],
        pause_reason=None,
    )

    assert result.status == RunStatus.FAILED
    assert result.tasks_total == 1
    assert result.tasks_compared == 0
    assert result.tasks_with_differences == 0
    assert result.details["partitions"] == []


def test_aggregate_matches_task_identity_with_sorted_market_key_items(tmp_path: Path) -> None:
    task = _task(key_values={"interval": "1m", "symbol": "BTCUSDT"})
    manifest_task = _task(key_values={"symbol": "BTCUSDT", "interval": "1m"})
    service = PartitionedAggregationService(tmp_path)

    result = service.aggregate(
        run_id="run-7",
        tasks=[task],
        manifests=[_manifest(manifest_task)],
        pause_reason=None,
    )

    assert result.status == RunStatus.PASSED
    assert result.tasks_compared == 1
