"""将两笔原始持仓交给真实查询计算，再清理本次自有输入快照。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from api.strategy_dashboard_source_fixture_api import SourceFixtureAPI
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository


def position_source_payload(account_id: int, actor_id: int, fixture_id: str) -> dict[str, Any]:
    """生成已观察到的原始业务模型；不含 totals、敞口或比率期望结果。"""
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    started = (now - timedelta(days=1)).isoformat()
    run_id, batch_id, definition_id = (fixture_id + suffix for suffix in ("_run", "_batch", "_definition"))
    account = {"id": account_id, "email": fixture_id + "@example.invalid", "status": "active",
               "admin_id": actor_id, "exchange": "questtest_fixture", "created_at": started, "updated_at": timestamp}
    profile = {"AccountID": account_id, "Name": fixture_id, "Status": "active", "Version": 1,
               "CreatedBy": actor_id, "BusinessOwnerID": actor_id, "AccountCode": fixture_id,
               "CreatedAt": started, "UpdatedAt": timestamp, "DefaultRiskConfig": {"enabled": False}}
    assignment = {"ID": run_id, "AccountID": account_id, "DefinitionID": definition_id,
                  "Status": "active", "StartedAt": started, "EndedAt": None,
                  "Runtime": {"actual_status": "stopped", "has_ever_started": True, "observed_at": timestamp}}
    snapshot = {"ID": fixture_id + "_snapshot", "AccountID": account_id, "AssignmentID": None,
                "ObservedAt": timestamp, "SourceKey": fixture_id, "SourceWatermark": fixture_id,
                "DataStatus": "complete", "Payload": {"currency": "USDT", "snapshot_time": timestamp,
                "margin_equity": "1000", "wallet_balance": "923", "unrealized_pnl": "77",
                "margin_used": "456", "precision_status": "exact"}}
    snapshot["Payload"]["field_status"] = {
        key: {"status": "available", "reason_code": None}
        for key in ("positions", "margin_equity", "wallet_balance", "unrealized_pnl", "margin_used", "snapshot_time")
    }
    batch = {"ID": batch_id, "AccountID": account_id, "AssignmentID": run_id,
             "DefinitionID": definition_id, "ObservedAt": timestamp, "Complete": True,
             "ExpectedCount": 2, "ActualCount": 2, "DataStatus": "complete",
             "SourceKey": fixture_id, "SourceSystem": "questtest_source_fixture", "Payload": {}}
    positions = []
    for index, (symbol, side, quantity, price, notional, margin, pnl) in enumerate((
        ("BTCUSDT", "long", "6", "100", "600", "100", "20"),
        ("ETHUSDT", "short", "-8", "50", "-400", "200", "-5"),
    )):
        positions.append({"ID": fixture_id + f"_pos{index}", "BatchID": batch_id,
                          "AssignmentID": run_id, "PositionKey": symbol + ":" + side,
                          "Payload": {"symbol": symbol, "position_side": side, "signed_qty": quantity,
                          "mark_price": price, "notional_usdt": notional, "margin_usdt": margin,
                          "unrealized_pnl_usdt": pnl}})
    return {"accounts": [account], "profiles": [profile], "assignments": [assignment],
            "definitions": [{"ID": definition_id, "Code": fixture_id, "Name": fixture_id, "Status": "active"}],
            "snapshots": [snapshot], "position_batches": [batch], "positions": positions,
            "bills": [], "receipts": [], "policies": [], "cycles": [], "events": [],
            "coverage": [], "account_access": [], "users": []}


class SourceFixtureFlow:
    """编排自有原始输入快照与真实持仓接口，不修改现有业务账户。"""

    def __init__(self, repository: SourceFixtureRepository, api: SourceFixtureAPI, actor_id: int,
                 evidence_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        """接收已配置的 DB/API 与当前真实登录人身份。"""
        self.repository, self.api, self.actor_id = repository, api, actor_id
        self.evidence_sink = evidence_sink

    @contextmanager
    def execute(self, *, source_positions: list[dict[str, Any]] | None = None,
                snapshot_overrides: dict[str, Any] | None = None,
                payload_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                metrics: str | None = None) -> Iterator[dict[str, Any]]:
        """插入原始业务输入、调用真实接口并清理；异常仍执行精确清理。"""
        fixture_id = "qfi_" + uuid4().hex
        account_id = None
        evidence: dict[str, Any] = {"fixture_id": fixture_id, "injection_boundary": "raw_minute_source_batch"}
        try:
            account_id = self.repository.create_account(fixture_id, self.actor_id)
            evidence["account_id"] = account_id
            payload = position_source_payload(account_id, self.actor_id, fixture_id)
            if snapshot_overrides:
                payload["snapshots"][0]["Payload"].update(snapshot_overrides)
            if payload_builder:
                payload = payload_builder(payload)
            positions = []
            if source_positions is None:
                for position in payload["positions"]:
                    raw = position["Payload"]
                    positions.append({"symbol": raw["symbol"], "currency": "USDT", "position_side": raw["position_side"],
                                      "signed_qty": raw["signed_qty"], "quantity": raw["signed_qty"].lstrip("-"),
                                      "mark_price": raw["mark_price"], "notional": raw["notional_usdt"],
                                      "margin_used": raw["margin_usdt"], "unrealized_pnl": raw["unrealized_pnl_usdt"]})
            else:
                for row in source_positions:
                    raw = dict(row)
                    raw["account_id"] = str(account_id)
                    raw["position_side"] = raw.pop("side", raw.get("position_side"))
                    if "quantity" in raw:
                        raw["signed_qty"] = raw["quantity"]
                        raw["quantity"] = str(raw["quantity"]).lstrip("-")
                    if "used_margin" in raw:
                        raw["margin_used"] = raw.pop("used_margin")
                    raw.setdefault("currency", "USDT")
                    positions.append(raw)
            for raw in positions:
                raw["field_status"] = {key: {"status": "available" if value is not None else "unavailable",
                                             "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE"}
                                       for key, value in raw.items() if key != "field_status"}
            evidence.update({"source": payload, "account_positions": positions})
            self.repository.insert_sources(fixture_id, account_id, payload["snapshots"][0], positions)
            self.repository.assert_not_current_batch(fixture_id)
            business = dict(payload, positions=None, position_batches=None, snapshots=None,
                            assignments=[], definitions=[])
            view_payload = {"version": 1, "batch_id": fixture_id + "_batch", "business": business,
                            "account_snapshot_ids": [fixture_id + "_snapshot"], "strategy_sample_ids": []}
            context = {"minute_mode": True, "minute_endpoint_positions": True, "selection_members": None,
                       "minute_missing_runs": {}, "minute_missing_accounts": {},
                       "minute_meta": {"generation": fixture_id + "_batch", "data_status": "complete",
                                       "as_of": payload["snapshots"][0]["ObservedAt"]}}
            self.repository.insert(fixture_id, self.actor_id, account_id, view_payload, context)
            evidence["summary_response"] = self.api.summary(account_id, fixture_id, metrics=metrics)
            yield evidence
        finally:
            try:
                evidence["cleanup"] = self.repository.delete(fixture_id, self.actor_id)
            finally:
                try:
                    if account_id is not None:
                        evidence["source_cleanup"] = self.repository.delete_sources(fixture_id, account_id)
                finally:
                    try:
                        if account_id is not None:
                            evidence["identity_cleanup"] = self.repository.delete_account(fixture_id, self.actor_id, account_id)
                            self.repository.assert_clean(fixture_id, account_id)
                            evidence["cleanup_verified_zero"] = True
                    finally:
                        if self.evidence_sink:
                            self.evidence_sink(evidence)
