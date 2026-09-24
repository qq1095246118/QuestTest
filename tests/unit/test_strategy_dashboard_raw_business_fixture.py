"""Owned-resource cleanup and raw-input preservation for the real history adapter."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from service.strategy_dashboard_raw_business_fixture import RawBusinessFixtureFlow
from service.strategy_dashboard_history_formula_inputs import history_input_plan, bind_history_payload, bind_realized_events
from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository


def _runtime():
    repository = Mock(spec=SourceFixtureRepository)
    repository.create_account.return_value = 910001
    api = Mock()
    api.summary.return_value = {"http_status": 200, "body": {}}
    sink = Mock()
    return repository, api, sink, RawBusinessFixtureFlow(repository, api, 7, sink)


def test_root_input_view_contains_raw_business_without_minute_wrapper():
    repository, api, sink, flow = _runtime()
    with flow.execute(lambda base: base, metrics="max_drawdown", context={"minute_mode": True}) as evidence:
        args = repository.insert.call_args.args
        assert "accounts" in args[3] and "business" not in args[3]
        assert args[4]["minute_mode"] is False
        assert evidence["injection_boundary"] == "raw_business_read_view"
    repository.delete.assert_called_once()
    repository.delete_account.assert_called_once()
    repository.assert_clean.assert_called_once()
    assert evidence["cleanup_verified_zero"] is True
    sink.assert_called_once()


@pytest.mark.parametrize("stage", ["builder", "insert", "summary", "assertion"])
def test_every_failure_boundary_cleans_owned_identity(stage):
    repository, api, sink, flow = _runtime()
    def builder(base):
        if stage == "builder":
            raise RuntimeError("builder")
        return base
    if stage == "insert":
        repository.insert.side_effect = RuntimeError("insert")
    if stage == "summary":
        api.summary.side_effect = RuntimeError("summary")
    with pytest.raises(RuntimeError):
        with flow.execute(builder, metrics="max_drawdown"):
            raise RuntimeError("assertion")
    repository.delete.assert_called_once()
    repository.delete_account.assert_called_once()
    repository.assert_clean.assert_called_once()
    sink.assert_called_once()


def test_failed_cleanup_is_not_reported_as_zero_residue():
    repository, api, sink, flow = _runtime()
    repository.assert_clean.side_effect = RuntimeError("residue")
    with pytest.raises(RuntimeError, match="residue"):
        with flow.execute(lambda base: base, metrics="max_drawdown"):
            pass
    assert "cleanup_verified_zero" not in sink.call_args.args[0]


def test_history_binding_preserves_separate_realized_and_unrealized_inputs():
    plan = history_input_plan({"G": "7", "D": "100", "days": "7",
                               "equity_path": ["100", "110", "97"]})
    base = {"accounts": [{"id": 1}], "profiles": [{"Name": "qfi_" + "a" * 32}]}
    raw = bind_history_payload(base, plan)
    final = raw["snapshots"][-1]["Payload"]
    assert final["margin_equity"] == "97"
    assert final["realized_pnl_gross"] == "7"
    assert final["wallet_balance"] == "107"
    assert final["unrealized_pnl"] == "-10"
    assert not {"annualized_return", "sharpe_ratio", "calmar_ratio", "volatility", "max_drawdown"} & final.keys()


@pytest.mark.parametrize("missing", ["G", "D"])
def test_missing_source_amount_is_never_replaced_with_zero(missing):
    inputs = {"G": "7", "D": "100", "days": "7", missing: None}
    base = {"accounts": [{"id": 1}], "profiles": [{"Name": "qfi_" + "a" * 32}]}
    raw = bind_history_payload(base, history_input_plan(inputs))
    key = {"G": "realized_pnl_gross", "D": "net_deposit"}[missing]
    assert all(row["Payload"][key] is None for row in raw["snapshots"])
    assert all(row["Payload"]["field_status"][key]["status"] == "unavailable" for row in raw["snapshots"])


def test_cycle_baseline_is_consumable_raw_snapshot_and_does_not_move_lifetime_start():
    inputs = {"G": "10", "D": "100", "Gbase": "9", "days": "10"}
    base = {"accounts": [{"id": 1}], "profiles": [{"Name": "qfi_" + "a" * 32, "CreatedBy": 7}]}
    plan = history_input_plan(inputs)
    raw = bind_history_payload(base, plan)
    cycle = raw["cycles"][0]
    baseline = next(row for row in raw["snapshots"] if row["ID"] == cycle["BaselineSnapshotID"])
    assert baseline["Payload"]["realized_pnl_gross"] == "9"
    assert raw["snapshots"][0]["Payload"]["realized_pnl_gross"] == "0"
    assert raw["snapshots"][-1]["Payload"]["realized_pnl_gross"] == "10"
    assert raw["accounts"][0]["created_at"] == plan["from"]
    assert (datetime.fromisoformat(cycle["StartedAt"]) - datetime.fromisoformat(plan["from"])).days == 9


@pytest.mark.parametrize("days", ["7", "7.5", "365", "366"])
def test_duration_is_not_compressed_to_number_of_equity_changes(days):
    plan = history_input_plan({"G": "7", "D": "100", "days": days, "equity_path": ["100", "120", "90", "100"]})
    delta = datetime.fromisoformat(plan["to"]) - datetime.fromisoformat(plan["from"])
    assert delta.total_seconds() == float(days) * 86400


def test_event_fixture_keeps_zero_events_and_outside_window_data_for_backend_filtering():
    base = {"accounts": [{"id": 1}], "profiles": [{"Name": "qfi_" + "a" * 32}]}
    raw = bind_realized_events(base, {"pnl": ["30", "-15", "0"], "outside_pnl": ["1000"]},
                               "2026-09-01T00:00:00+00:00", "2026-09-09T00:00:00+00:00")
    assert [event["Payload"]["realized_pnl_gross"] for event in raw["events"]] == ["30", "-15", "0", "1000"]
    assert raw["events"][-1]["EventAt"] < raw["coverage"][0]["FromAt"]
    assert all("win_rate" not in event["Payload"] and "profit_loss_ratio" not in event["Payload"] for event in raw["events"])
