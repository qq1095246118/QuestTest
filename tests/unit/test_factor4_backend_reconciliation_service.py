"""三方对账的表示归一和时间错位反例；不代表 live 通过。"""

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from api.factor_data_mcp_api import MCPResponse
from api.factor4_backend_api import Factor4BackendAPI
from service.factor4_backend_reconciliation_service import Factor4BackendReconciliationService
from service.factor4_summary_service import _SUMMARY_FIELDS
from service.factor4_read_service import ReadContractError, ReadPrecondition

pytestmark = pytest.mark.unit


def fixture_backend(*, wrong_period: bool = False, ic_scope: str = "time_series") -> tuple[Factor4BackendReconciliationService, dict[str, Any]]:
    """创建数值字符串 Backend、数字 MCP 和 Decimal DB；可注入错误时间，无真实请求。"""
    row = dict.fromkeys(_SUMMARY_FIELDS)
    row.update({"id": 1, "factor_id": 2, "is_sub_factor_id": 1, "run_id": "run-a", "ic_scope": "time_series", "calculation_mode": "direct", "factor_bar_interval": "1h", "factor_window_bars": "24", "return_bar_interval": "1h", "forward_return_bars": 1, "universe_key": "all", "symbol": "", "window_scope": "full", "scoring_version": "v1", "mean_ic": Decimal("0.123456789"), "metrics_json": {"summary": {"period_start": "2025-01-01T00:00:00Z", "period_end": "2025-01-02T00:00:00Z"}}})
    row.update(ic_scope=ic_scope, _sample_run_status="completed")
    identity = {key: row[key] for key in ("id", "factor_id", "is_sub_factor_id", "run_id", "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version")}
    backend_row = {**identity, "mean_ic": "0.123456789", "period_start": "2025-01-01T00:00:00+08:00" if wrong_period else "2025-01-01T08:00:00+08:00", "period_end": "2025-01-02T08:00:00+08:00"}
    mcp_row = {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items() if key != "metrics_json"}
    mcp_row.update(row["metrics_json"]["summary"])
    class Backend:
        items = [backend_row]
        calls: list[tuple[int | None, bool, dict[str, Any]]]

        def __init__(self) -> None:
            self.calls = []

        def summary_metrics(self, factor_id: int | None, is_sub_factor: bool, **kwargs: Any) -> Any:
            """返回合法的有界 Backend projection，无网络。"""
            self.calls.append((factor_id, is_sub_factor, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: {"data": {"items": self.items}})
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


@pytest.mark.parametrize("ic_scope", ["time_series", "cross_sectional"])
def test_public_backend_sample_keeps_same_row_and_all_reconciliation(ic_scope: str) -> None:
    """A one-row public result drives exact DB/MCP evidence, without a second Backend selection or history gate."""
    service, row = fixture_backend(ic_scope=ic_scope)
    ids: list[int] = []

    def by_id(identity: int) -> dict[str, Any]:
        ids.append(identity)
        return row

    result = service.check_summary_sample(SimpleNamespace(summary_by_id=by_id), ic_scope)
    assert not result.issues and result.checked_count == 2
    assert ids == [1]
    assert service.backend.calls == [(None, True, {"ic_scope": ic_scope, "limit": 1})]


@pytest.mark.parametrize("defect,expected", [
    ("missing_id", "public summary id"),
    ("wrong_scope", "requested TS/CS scope mismatch"),
    ("wrong_type", "requested sub-factor identity mismatch"),
    ("missing_db", "has no DB row"),
    ("wrong_db_scope", "conflicts with DB row"),
    ("missing_run", "no associated DB Run"),
    ("oversized_page", "exceeds requested limit"),
])
def test_public_sample_identity_failures_are_not_sample_absence(defect: str, expected: str) -> None:
    """Missing/broken returned identities fail before a replacement DB sample can mask them."""
    service, row = fixture_backend()
    public = service.backend.items[0]
    if defect == "missing_id":
        public.pop("id")
    elif defect == "wrong_scope":
        public["ic_scope"] = "cross_sectional"
    elif defect == "wrong_type":
        public["is_sub_factor_id"] = False
    elif defect == "wrong_db_scope":
        row["ic_scope"] = "cross_sectional"
    elif defect == "missing_run":
        row["_sample_run_status"] = None
    elif defect == "oversized_page":
        service.backend.items = [public, public]
    repository = SimpleNamespace(summary_by_id=lambda _: None if defect == "missing_db" else row)
    with pytest.raises(ReadContractError, match=expected):
        service.check_summary_sample(repository, "time_series")


@pytest.mark.parametrize("defect", ["period", "value", "factor", "run"])
def test_public_sample_does_not_mask_original_value_identity_or_period_defects(defect: str) -> None:
    """The public selection path retains the original exact-value, typed identity, Run and timezone assertions."""
    service, row = fixture_backend(wrong_period=defect == "period")
    public = service.backend.items[0]
    if defect == "value":
        public["mean_ic"] = "0.9"
    elif defect == "factor":
        public["factor_id"] = 999
    elif defect == "run":
        public["run_id"] = "wrong-run"
    result = service.check_summary_sample(SimpleNamespace(summary_by_id=lambda _: row), "time_series")
    field = {"period": "period_instant=period_start", "value": "mean_ic", "factor": "factor_id", "run": "run_id"}[defect]
    assert any(field in issue and issue.startswith("backend:") for issue in result.issues)


@pytest.mark.parametrize("defect,expected", [
    ("empty", "returned no time_series"),
    ("unfinished", "Run is not completed"),
    ("period", "period lacks explicit timezone"),
])
def test_public_sample_reports_real_preconditions_without_fabricating_evidence(defect: str, expected: str) -> None:
    """An empty public page, unfinished result, or absent timezone evidence remains explicit incompleteness."""
    service, row = fixture_backend()
    if defect == "empty":
        service.backend.items = []
    elif defect == "unfinished":
        row["_sample_run_status"] = "running"
    else:
        row["metrics_json"] = {"summary": {"period_start": "2025-01-01", "period_end": "2025-01-02"}}
    with pytest.raises(ReadPrecondition, match=expected):
        service.check_summary_sample(SimpleNamespace(summary_by_id=lambda _: row), "time_series")


def test_backend_public_sample_request_uses_only_documented_selectors() -> None:
    """An unbound factor sample uses the documented type/scope/limit contract, without invented Run filtering."""
    calls: list[tuple[str, str, dict[str, Any]]] = []
    client = SimpleNamespace(request=lambda method, path, **kwargs: calls.append((method, path, kwargs)))
    Factor4BackendAPI(client).summary_metrics(None, True, ic_scope="cross_sectional", limit=1)
    assert calls == [("GET", "/factor-ic/summary-metrics", {
        "params": {"is_sub_factor_id": True, "ic_scope": "cross_sectional", "limit": 1},
    })]
