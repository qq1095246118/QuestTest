from pathlib import Path

import allure
import pytest

from services.db_accuracy.partitioned.models import (
    AccuracyMode,
    CachePolicy,
    ExecutionOptions,
    PartitionedAccuracyRequest,
)
from services.db_accuracy.partitioned.runner_service import PartitionedAccuracyService
from services.db_accuracy.reporting.result_serializer_service import ResultSerializerService


pytestmark = pytest.mark.db_accuracy


@allure.title("DB-ACC-BINANCE-FULL-001 - Binance raw/metadata DB rows match upstream REST source")
@pytest.mark.dqc
def test_binance_raw_and_metadata_db_accuracy(request):
    """
    Case ID: DB-ACC-BINANCE-FULL-001
    测试目的: 全量校验 PDF 范围内 Binance raw/metadata 表中的稳定历史数据与 Binance REST 上游严格一致。
    """
    partitioned_request = _partitioned_compare_request(request.config)
    result = PartitionedAccuracyService().run(partitioned_request)

    allure.attach(
        result.summary_text,
        name="db_accuracy_partitioned_summary",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        ResultSerializerService.partitioned_to_json(result),
        name="db_accuracy_partitioned_details",
        attachment_type=allure.attachment_type.JSON,
    )

    assert result.passed, result.summary_text


def _partitioned_compare_request(config) -> PartitionedAccuracyRequest:
    return PartitionedAccuracyRequest(
        mode=AccuracyMode(config.getoption("--db-accuracy-mode")),
        tables=tuple(config.getoption("--db-accuracy-table")),
        start_ms=config.getoption("--db-accuracy-start-ms"),
        end_ms=config.getoption("--db-accuracy-end-ms"),
        cache_root=Path(config.getoption("--db-accuracy-cache-root")),
        symbols=tuple(config.getoption("--db-accuracy-symbol")),
        pairs=tuple(config.getoption("--db-accuracy-pair")),
        contract_types=tuple(config.getoption("--db-accuracy-contract-type")),
        intervals=tuple(config.getoption("--db-accuracy-interval")),
        partition_days=config.getoption("--db-accuracy-partition-days"),
        max_shards=config.getoption("--db-accuracy-max-shards"),
        safety_hours=config.getoption("--db-accuracy-safety-hours"),
        cache_policy=CachePolicy(
            use_db_cache=config.getoption("--db-accuracy-use-db-cache"),
            use_source_cache=config.getoption("--db-accuracy-use-source-cache"),
        ),
        execution=ExecutionOptions(
            workers=config.getoption("--db-accuracy-workers"),
            source_retries=config.getoption("--db-accuracy-source-retries"),
            source_retry_backoff_ms=config.getoption("--db-accuracy-source-retry-backoff-ms"),
            stop_on_source_failure=config.getoption("--db-accuracy-stop-on-source-failure"),
        ),
    )
