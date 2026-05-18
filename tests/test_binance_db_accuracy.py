import allure
import pytest

from tests.db_accuracy.runner import AccuracyRunner, result_to_json


pytestmark = pytest.mark.db_accuracy


@allure.title("DB-ACC-BINANCE-FULL-001 - Binance raw/metadata DB rows match upstream REST source")
@pytest.mark.dqc
def test_binance_raw_and_metadata_db_accuracy(request):
    """
    Case ID: DB-ACC-BINANCE-FULL-001
    测试目的: 全量校验 PDF 范围内 Binance raw/metadata 表中的稳定历史数据与 Binance REST 上游严格一致。
    """
    safety_hours = request.config.getoption("--db-accuracy-safety-hours")
    include_tables = request.config.getoption("--db-accuracy-table")

    result = AccuracyRunner().run(
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
