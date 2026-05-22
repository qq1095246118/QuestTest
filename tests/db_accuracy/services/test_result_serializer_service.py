from services.db_accuracy.cached.cache_models import CachedRunResult, CachedShardResult
from services.db_accuracy.models import AccuracyRunResult, Difference, TableRunResult
from services.db_accuracy.partitioned.models import PartitionedRunResult, RunStatus
from services.db_accuracy.reporting.result_serializer_service import ResultSerializerService


def test_direct_to_json_includes_difference_reason():
    result = AccuracyRunResult(
        tables=[
            TableRunResult(
                table="sample_table",
                differences=[
                    Difference(
                        table="sample_table",
                        key_label="symbol=BTCUSDT",
                        row_key=1,
                        field="close",
                        db_value="1",
                        source_value="2",
                        reason="value_mismatch",
                    )
                ],
            )
        ]
    )

    payload = ResultSerializerService.direct_to_json(result)

    assert '"passed": false' in payload
    assert '"reason": "value_mismatch"' in payload


def test_cached_to_json_includes_shard_status():
    result = CachedRunResult(
        shards=[
            CachedShardResult(
                shard_label="table=t",
                partition_label="1-2",
                status="passed",
                db_rows=1,
                source_rows=1,
                differences=0,
                report_path="report.txt",
                diff_path="diff.json",
                message=None,
            )
        ]
    )

    payload = ResultSerializerService.cached_to_json(result)

    assert '"passed": true' in payload
    assert '"status": "passed"' in payload


def test_partitioned_to_json_serializes_details_without_recomputing_summary():
    result = PartitionedRunResult(
        status=RunStatus.PASSED,
        tasks_total=1,
        tasks_compared=1,
        tasks_with_differences=0,
        db_rows=2,
        source_rows=2,
        differences=0,
        summary_text="status=passed",
        details={"status": "passed", "partitions": [{"label": "p1"}]},
    )

    payload = ResultSerializerService.partitioned_to_json(result)

    assert '"status": "passed"' in payload
    assert '"label": "p1"' in payload
