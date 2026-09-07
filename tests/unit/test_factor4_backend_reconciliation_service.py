"""三方对账的表示归一和时间错位反例；不代表 live 通过。"""

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from service.factor4_backend_reconciliation_service import Factor4BackendReconciliationService
from service.factor4_summary_service import _SUMMARY_FIELDS
from service.factor4_read_service import ReadPrecondition

pytestmark = pytest.mark.unit


def fixture_backend(*, wrong_period: bool = False) -> tuple[Factor4BackendReconciliationService, dict[str, Any]]:
    """创建数值字符串 Backend、数字 MCP 和 Decimal DB；可注入错误时间，无真实请求。"""
    row = dict.fromkeys(_SUMMARY_FIELDS)
    row.update({"id": 1, "factor_id": 2, "is_sub_factor_id": 1, "run_id": "run-a", "ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h", "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1, "universe_key": "all", "symbol": "", "window_scope": "full", "scoring_version": "v1", "mean_ic": Decimal("0.123456789"), "metrics_json": {"summary": {"period_start": "2025-01-01T00:00:00Z", "period_end": "2025-01-02T00:00:00Z"}}})
    identity = {key: row[key] for key in ("id", "factor_id", "is_sub_factor_id", "run_id", "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version")}
    backend_row = {**identity, "mean_ic": "0.123456789", "period_start": "2025-01-01T00:00:00+08:00" if wrong_period else "2025-01-01T08:00:00+08:00", "period_end": "2025-01-02T08:00:00+08:00"}
    mcp_row = {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items() if key != "metrics_json"}
    mcp_row.update(row["metrics_json"]["summary"])
    class Backend:
        def summary_metrics(self, factor_id: int, is_sub_factor: bool) -> Any:
            """返回合法的有界 Backend projection，无网络。"""
            return SimpleNamespace(status_code=200, json=lambda: {"data": {"items": [backend_row]}})
    class MCP:
        def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
            """返回完整双表示 summary，无网络。"""
            body = {"data": {"ic_summaries": [mcp_row]}, "meta": {}}
            return MCPResponse(200, None, {"result": {"structuredContent": body, "content": [{"type": "text", "text": json.dumps(body)}]}}, None)
    return Factor4BackendReconciliationService(Backend(), SimpleNamespace(api=SimpleNamespace(mcp=MCP()))), row  # type: ignore[arg-type]


def test_backend_numeric_strings_and_equivalent_timezone_offsets_are_not_bugs() -> None:
    service, row = fixture_backend()
    assert not service.check_summary(row).issues


def test_backend_wrong_period_instant_is_not_masked_as_display_format() -> None:
    service, row = fixture_backend(wrong_period=True)
    assert service.check_summary(row).issues == ("backend:period_instant=period_start",)


@pytest.mark.parametrize("wrong_values", [False, True])
def test_missing_period_evidence_never_hides_confirmed_value_mismatch(wrong_values: bool) -> None:
    """Unknown time semantics block only when all comparable persisted values agree."""
    service, row = fixture_backend()
    row["metrics_json"] = {"summary": {"period_start": "2025-01-01", "period_end": "2025-01-02"}}
    if wrong_values:
        row["mean_ic"] = Decimal("0.9")
        check = service.check_summary(row)
        assert any("mean_ic" in issue for issue in check.issues)
        assert check.evidence["blocked"] == ("summary_period_timezone_missing",)
    else:
        with pytest.raises(ReadPrecondition, match="period lacks explicit timezone"):
            service.check_summary(row)
