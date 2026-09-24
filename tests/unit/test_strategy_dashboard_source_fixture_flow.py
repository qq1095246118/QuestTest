"""离线验证原始输入编排、隔离和故障清理；不计作真实产品公式通过。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from db.client import DatabaseClient
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository
from service.strategy_dashboard_source_fixture_flow import SourceFixtureFlow, position_source_payload
from tests.strategy_dashboard_formula_assertions import assert_formula_values


pytestmark = pytest.mark.unit
FIXTURE_ID = "qfi_" + "a" * 32
ACTOR_ID = 7
ACCOUNT_ID = 93001


class _Repository:
    """保留资源集合的内存替身，支持阶段失败，不连接数据库。"""

    def __init__(self, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[Any, ...]] = []
        self.resources: set[str] = set()
        self.source: dict[str, Any] | None = None
        self.view: dict[str, Any] | None = None

    def _step(self, stage: str, *identity: Any) -> None:
        self.calls.append((stage, *identity))
        if self.fail_at == stage:
            raise RuntimeError("synthetic " + stage)

    def create_account(self, fixture_id: str, actor_id: int) -> int:
        """分配固定独立账户；故障发生在分配前。"""
        self._step("create_account", fixture_id, actor_id)
        self.resources.add("account")
        return ACCOUNT_ID

    def insert_sources(self, fixture_id: str, account_id: int, snapshot: dict[str, Any],
                       positions: list[dict[str, Any]]) -> None:
        """保留真实输入副本；模拟写入成功但返回失败的清理路径。"""
        self.resources.add("sources")
        self.source = deepcopy({"snapshot": snapshot, "positions": positions})
        self._step("insert_sources", fixture_id, account_id)

    def assert_not_current_batch(self, fixture_id: str) -> None:
        """记录共享批次隔离检查，支持失败。"""
        self._step("assert_not_current_batch", fixture_id)

    def insert(self, fixture_id: str, actor_id: int, account_id: int,
               payload: dict[str, Any], context: dict[str, Any]) -> None:
        """保留查询输入，不生成任何计算结果。"""
        self.resources.add("view")
        self.view = deepcopy({"payload": payload, "context": context})
        self._step("insert", fixture_id, actor_id, account_id)

    def delete(self, fixture_id: str, actor_id: int) -> dict[str, int]:
        """删除本次视图，返回删除数量。"""
        self._step("delete", fixture_id, actor_id)
        present = int("view" in self.resources)
        self.resources.discard("view")
        return {"views": present, "pages": 0}

    def delete_sources(self, fixture_id: str, account_id: int) -> dict[str, int]:
        """删除本次源数据，返回删除数量。"""
        self._step("delete_sources", fixture_id, account_id)
        present = int("sources" in self.resources)
        self.resources.discard("sources")
        return {"batches": present, "snapshots": present}

    def delete_account(self, fixture_id: str, actor_id: int, account_id: int) -> dict[str, int]:
        """删除专用账户，不接受其他账户作为目标。"""
        self._step("delete_account", fixture_id, actor_id, account_id)
        assert account_id == ACCOUNT_ID
        present = int("account" in self.resources)
        self.resources.discard("account")
        return {"accounts": present, "profiles": present}

    def assert_clean(self, fixture_id: str, account_id: int) -> None:
        """存在残留或主动模拟失败时抛错。"""
        self._step("assert_clean", fixture_id, account_id)
        if self.resources:
            raise RuntimeError("synthetic residual: " + ",".join(sorted(self.resources)))


class _API:
    """返回固定响应，不从输入重算公式，避免替身自证。"""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[int, str, str | None]] = []
        self.result = {"http_status": 200, "body": {"wallet": "923"}}

    def summary(self, account_id: int, fixture_id: str, *, metrics: str | None = None) -> dict[str, Any]:
        """记录真实身份参数，按需模拟读取超时。"""
        self.calls.append((account_id, fixture_id, metrics))
        if self.fail:
            raise requests.Timeout("synthetic HTTP timeout")
        return deepcopy(self.result)


def _build(fail_at: str | None = None) -> tuple[_Repository, _API, list[dict[str, Any]], SourceFixtureFlow]:
    repository, api, saved = _Repository(fail_at), _API(), []
    flow = SourceFixtureFlow(repository, api, ACTOR_ID, evidence_sink=lambda evidence: saved.append(deepcopy(evidence)))
    return repository, api, saved, flow


def _assert_no_expected_outputs(value: Any) -> None:
    if isinstance(value, dict):
        assert not {"expected", "expected_result", "totals", "summary_response", "result"}.intersection(value)
        for child in value.values():
            _assert_no_expected_outputs(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_expected_outputs(child)


def _exception_chain(error: BaseException) -> str:
    messages, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        messages.append(str(error))
        error = error.__cause__ or error.__context__
    return " | ".join(messages)


def test_source_flow_writes_inputs_only_and_keeps_exact_cleanup_identity() -> None:
    repository, api, saved, flow = _build()
    with flow.execute(metrics="wallet_balance") as evidence:
        fixture_id = evidence["fixture_id"]
        SourceFixtureRepository._require_id(fixture_id)
        assert evidence["summary_response"] == api.result
        assert repository.resources == {"account", "sources", "view"}
        _assert_no_expected_outputs(repository.source)
        _assert_no_expected_outputs(repository.view)
    assert not repository.resources
    assert api.calls == [(ACCOUNT_ID, fixture_id, "wallet_balance")]
    assert all(call[1] == fixture_id for call in repository.calls)
    assert ("delete", fixture_id, ACTOR_ID) in repository.calls
    assert ("delete_sources", fixture_id, ACCOUNT_ID) in repository.calls
    assert ("delete_account", fixture_id, ACTOR_ID, ACCOUNT_ID) in repository.calls
    assert len(saved) == 1
    assert saved[0]["fixture_id"] == fixture_id
    assert saved[0]["account_id"] == ACCOUNT_ID
    assert saved[0]["cleanup_verified_zero"] is True


@pytest.mark.parametrize("stage", ["payload_builder", "insert_sources", "assert_not_current_batch", "insert", "http"])
def test_failure_after_account_allocation_cleans_and_keeps_identity_evidence(stage: str) -> None:
    repository, api, saved, flow = _build(stage)
    options: dict[str, Any] = {}
    if stage == "payload_builder":
        def fail_builder(payload: dict[str, Any]) -> dict[str, Any]:
            assert repository.resources == {"account"}
            raise RuntimeError("synthetic payload_builder")
        options["payload_builder"] = fail_builder
    elif stage == "http":
        api.fail = True
    with pytest.raises((RuntimeError, requests.Timeout), match="synthetic"):
        with flow.execute(**options):
            pytest.fail("failing setup must not yield a successful execution")
    assert not repository.resources
    assert len(saved) == 1
    assert saved[0]["fixture_id"].startswith("qfi_")
    assert saved[0]["account_id"] == ACCOUNT_ID
    assert saved[0]["identity_cleanup"] == {"accounts": 1, "profiles": 1}
    assert saved[0]["cleanup_verified_zero"] is True
    if stage != "http":
        assert not api.calls


def test_failed_business_formula_assertion_still_cleans_and_saves_wrong_response() -> None:
    repository, api, saved, flow = _build()
    api.result["body"]["wallet"] = "999"
    with pytest.raises(AssertionError, match="wallet"):
        with flow.execute() as evidence:
            assert_formula_values(evidence["summary_response"]["body"], {"wallet": "923"})
    assert not repository.resources
    assert saved[0]["summary_response"]["body"]["wallet"] == "999"
    assert saved[0]["cleanup_verified_zero"] is True


@pytest.mark.parametrize("stage", ["delete", "delete_sources", "delete_account", "assert_clean"])
def test_cleanup_failures_are_not_swallowed_and_still_preserve_evidence(stage: str) -> None:
    repository, _, saved, flow = _build(stage)
    with pytest.raises(RuntimeError) as captured:
        with flow.execute():
            pass
    assert stage in _exception_chain(captured.value)
    assert len(saved) == 1
    assert saved[0]["account_id"] == ACCOUNT_ID
    assert saved[0].get("cleanup_verified_zero") is not True
    cleanup_calls = [call[0] for call in repository.calls if call[0].startswith("delete")]
    assert cleanup_calls == ["delete", "delete_sources", "delete_account"]


def test_business_failure_and_cleanup_failure_both_remain_visible() -> None:
    _, _, saved, flow = _build("delete_sources")
    with pytest.raises((RuntimeError, AssertionError)) as captured:
        with flow.execute():
            raise AssertionError("business formula mismatch")
    chain = _exception_chain(captured.value)
    assert "business formula mismatch" in chain
    assert "delete_sources" in chain
    assert saved[0].get("cleanup_verified_zero") is not True


def test_snapshot_summary_values_cannot_accidentally_equal_position_expectations() -> None:
    payload = position_source_payload(ACCOUNT_ID, ACTOR_ID, FIXTURE_ID)
    snapshot = payload["snapshots"][0]["Payload"]
    positions = [row["Payload"] for row in payload["positions"]]
    assert sum(Decimal(row["unrealized_pnl_usdt"]) for row in positions) == Decimal("15")
    assert sum(Decimal(row["margin_usdt"]) for row in positions) == Decimal("300")
    assert Decimal(snapshot["unrealized_pnl"]) != Decimal("15")
    assert Decimal(snapshot["margin_used"]) != Decimal("300")
    assert Decimal(snapshot["margin_equity"]) - Decimal(snapshot["unrealized_pnl"]) == Decimal(snapshot["wallet_balance"])
    _assert_no_expected_outputs(payload)


def test_custom_source_rows_keep_signed_inputs_and_do_not_mutate_scenario_data() -> None:
    source = [{"symbol": "SOLUSDT", "side": "short", "quantity": "-2.5", "mark_price": "4",
               "notional": "-10", "used_margin": None, "unrealized_pnl": "-1.25"}]
    original = deepcopy(source)
    repository, _, _, flow = _build()
    with flow.execute(source_positions=source, snapshot_overrides={"unrealized_pnl": "99"}):
        raw = repository.source["positions"][0]
        assert raw["signed_qty"] == "-2.5"
        assert raw["quantity"] == "2.5"
        assert raw["position_side"] == "short"
        assert raw["account_id"] == str(ACCOUNT_ID)
        assert raw["margin_used"] is None
        assert raw["field_status"]["margin_used"]["status"] == "unavailable"
        assert repository.source["snapshot"]["Payload"]["unrealized_pnl"] == "99"
    assert source == original


def test_production_repository_rejects_before_any_database_access() -> None:
    client = MagicMock(spec=DatabaseClient)
    with pytest.raises(ValueError, match="test environment"):
        SourceFixtureRepository(client, environment="production")
    assert not client.mock_calls


@pytest.mark.parametrize("change", [{"ID": "someone_else_snapshot"}, {"AccountID": ACCOUNT_ID + 1}])
def test_foreign_snapshot_is_rejected_before_transaction(change: dict[str, Any]) -> None:
    client = MagicMock(spec=DatabaseClient)
    repository = SourceFixtureRepository(client, environment="test")
    snapshot = position_source_payload(ACCOUNT_ID, ACTOR_ID, FIXTURE_ID)["snapshots"][0]
    snapshot.update(change)
    with pytest.raises(ValueError, match="identity"):
        repository.insert_sources(FIXTURE_ID, ACCOUNT_ID, snapshot, [])
    assert not client.mock_calls


@pytest.mark.parametrize("row", [
    {"actor_id": ACTOR_ID + 1, "context": {"questtest_fixture_id": FIXTURE_ID}},
    {"actor_id": ACTOR_ID, "context": {"questtest_fixture_id": "different_fixture"}},
])
def test_foreign_read_view_is_retained_without_delete(row: dict[str, Any]) -> None:
    client = MagicMock(spec=DatabaseClient)
    transaction = client.transaction.return_value.__enter__.return_value
    transaction.fetch_one.return_value = row
    with pytest.raises(ValueError, match="ownership"):
        SourceFixtureRepository(client, environment="test").delete(FIXTURE_ID, ACTOR_ID)
    transaction.execute.assert_not_called()


def test_orphan_read_page_prevents_claiming_zero_residue() -> None:
    client = MagicMock(spec=DatabaseClient)
    transaction = client.transaction.return_value.__enter__.return_value
    transaction.fetch_one.side_effect = [{"n": 0}] * 5 + [{"n": 1}]
    with pytest.raises(RuntimeError, match="residual"):
        SourceFixtureRepository(client, environment="test").assert_clean(FIXTURE_ID, ACCOUNT_ID)
    transaction.execute.assert_not_called()


def test_fixture_detected_as_shared_current_batch_is_rejected() -> None:
    client = MagicMock(spec=DatabaseClient)
    transaction = client.transaction.return_value.__enter__.return_value
    transaction.fetch_one.return_value = {"id": FIXTURE_ID + "_batch"}
    with pytest.raises(RuntimeError, match="shared current"):
        SourceFixtureRepository(client, environment="test").assert_not_current_batch(FIXTURE_ID)
    transaction.execute.assert_not_called()
