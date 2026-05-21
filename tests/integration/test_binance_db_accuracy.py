from pathlib import Path

import allure
import pytest

from services.db_accuracy.cached.cache_models import CachedCompareRequest
from services.db_accuracy.cached.cached_accuracy_service import CachedAccuracyService, cached_result_to_json
from services.db_accuracy.direct.accuracy_service import DirectAccuracyService, result_to_json
from services.db_accuracy.cached.shard_planner_service import validate_cached_request


pytestmark = pytest.mark.db_accuracy


@allure.title("DB-ACC-BINANCE-FULL-001 - Binance raw/metadata DB rows match upstream REST source")
@pytest.mark.dqc
def test_binance_raw_and_metadata_db_accuracy(request):
    """
    Case ID: DB-ACC-BINANCE-FULL-001
    测试目的: 全量校验 PDF 范围内 Binance raw/metadata 表中的稳定历史数据与 Binance REST 上游严格一致。
    """
    mode = request.config.getoption("--db-accuracy-mode")
    if mode == "cached":
        cached_request = _cached_compare_request(request.config)
        validate_cached_request(cached_request)
        result = CachedAccuracyService().run(cached_request)

        allure.attach(
            result.summary_text(),
            name="db_accuracy_cached_summary",
            attachment_type=allure.attachment_type.TEXT,
        )
        allure.attach(
            cached_result_to_json(result),
            name="db_accuracy_cached_details",
            attachment_type=allure.attachment_type.JSON,
        )

        assert result.passed, result.summary_text()
        return

    safety_hours = request.config.getoption("--db-accuracy-safety-hours")
    include_tables = request.config.getoption("--db-accuracy-table")

    result = DirectAccuracyService().run(
        safety_hours=safety_hours,
        include_tables=include_tables,
    )

    allure.attach(
        result.summary_text(),
        name="db_accuracy_summary",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        result_to_json(result),
        name="db_accuracy_details",
        attachment_type=allure.attachment_type.JSON,
    )

    assert result.passed, result.summary_text()


def _single_table(tables: list[str]) -> str:
    if len(tables) != 1:
        raise ValueError(
            "cached DB accuracy mode requires exactly one --db-accuracy-table"
        )
    return tables[0]


def _cached_compare_request(config) -> CachedCompareRequest:
    return CachedCompareRequest(
        table=_single_table(config.getoption("--db-accuracy-table")),
        start_ms=config.getoption("--db-accuracy-start-ms"),
        end_ms=config.getoption("--db-accuracy-end-ms"),
        cache_root=Path(config.getoption("--db-accuracy-cache-root")),
        symbols=tuple(config.getoption("--db-accuracy-symbol")),
        pairs=tuple(config.getoption("--db-accuracy-pair")),
        contract_types=tuple(config.getoption("--db-accuracy-contract-type")),
        intervals=tuple(config.getoption("--db-accuracy-interval")),
        partition_days=config.getoption("--db-accuracy-partition-days"),
        refresh_cache=config.getoption("--db-accuracy-refresh-cache"),
        max_shards=config.getoption("--db-accuracy-max-shards"),
    )
