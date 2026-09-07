"""离线验证公式证据完整性迁移逻辑。"""

from dataclasses import replace

import pytest

from service.factor4_calculation_service import Factor4CalculationService
from tests.unit.test_factor4_calculation_service import _formula, _service, _snapshot


pytestmark = pytest.mark.unit


def test_formula_integrity_accepts_completed_windowed_evidence() -> None:
    """合法 completed 证据、hash/version、字段和窗口调用应通过。"""

    evidence = _formula(expression="close.rolling(window=24).mean()")
    service = _service(_snapshot(formulas=(evidence,)))[0]
    result = service.check_formula_integrity(_snapshot(formulas=(evidence,)))
    assert result.status == "PASS"
    assert result.checked_count == 1


def test_formula_integrity_rejects_missing_window_call() -> None:
    """rolling/VWAP 不带窗口必须被识别为业务公式缺陷。"""

    evidence = _formula(expression="close.vwap()")
    snapshot = _snapshot(formulas=(evidence,))
    service = _service(snapshot)[0]
    result = service.check_formula_integrity(snapshot)
    assert result.status == "FAIL"
    assert any(item.code == "FORMULA_WINDOW_MISSING" for item in result.findings)


def test_formula_integrity_blocks_unfinished_or_incomplete_metadata() -> None:
    """未完成 Run 或缺失不可变元数据不能被当作可执行证据。"""

    evidence = replace(_formula(), run_status="running", formula_hash="", metadata_complete=False,
                       required_fields=())
    snapshot = _snapshot(formulas=(evidence,))
    service = _service(snapshot)[0]
    result = service.check_formula_integrity(snapshot)
    assert result.status == "BLOCKED_DATA_PRECONDITION"
    codes = {item.code for item in result.findings}
    assert {"FORMULA_RUN_NOT_COMPLETED", "FORMULA_IDENTITY_METADATA_MISSING",
            "FORMULA_REQUIRED_FIELDS_MISSING", "FORMULA_METADATA_INCOMPLETE"} <= codes

