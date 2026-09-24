"""Independent Decimal reference calculations; never a substitute for a live result.

Inputs use the logical raw-fixture vocabulary documented by the scenario registry.
No production implementation or result value is imported. Missing dependencies stay
null, contract-dependent calculations stay blocked, and arithmetic uses a local
context so importing this module cannot change another test's Decimal precision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Callable, Mapping


_FIELDS: dict[str, tuple[str, ...]] = {
    "wallet": ("wallet",),
    "net_deposits": ("net_deposit",),
    "cumulative_return": ("cumulative_return",),
    "interval_profit": ("interval_net_profit",),
    "post_clear_return": ("post_clear_return",),
    "billing_principal": ("billing_principal",),
    "billing_profit": ("billing_profit",),
    "billing_return": ("billing_return",),
    "annualized_return": ("annualized_return",),
    "daily_returns": ("daily_returns",),
    "annualized_volatility": ("annualized_volatility", "sample_count"),
    "sharpe": ("sharpe",),
    "calmar": ("calmar",),
    "drawdown": ("current_drawdown", "max_drawdown"),
    "nav": ("cumulative_nav", "cycle_nav"),
    "contribution": ("denominator", "contributions"),
    "plan_match": ("target_count", "matched_count", "match_rate"),
    "order_completion": ("order_count", "completed_count", "completion_rate"),
    "weighted_fill_price": ("weighted_average_price",),
    "positions": ("position_count", "gross_notional"),
    "execution_latency": ("latency_seconds",),
    "execution_latest_times": ("latest_triggered_at", "latest_completed_at"),
    "receivable": ("receivable",),
    "billing_gate": ("eligible", "billing_principal", "billing_profit", "billing_return"),
    "receipts": ("received", "outstanding", "latest_received_at"),
    "overdue": ("overdue",),
    "bill_summary": ("bill_count", "total_receivable", "received", "outstanding", "collection_progress", "latest_received_at"),
    "account_totals": ("account_count", "total_equity"),
    "position_totals": ("unrealized_pnl", "used_margin"),
    "health_weighted_scores": ("health_score",),
    "ledger_collection": ("bill_count", "receivable_amount", "received_amount", "outstanding_amount", "collection_progress"),
    "receipt_registration": ("registered_received_amount", "pending_received_amount", "receipt_count", "last_registered_at"),
    "net_deposit_change": ("net_deposit_change",),
}

_UNCONFIRMED = frozenset({
    "health", "sortino", "win_rate", "profit_factor", "fx_fees",
    "first_baseline", "runtime_day_policy", "fixed_principal_nav_cash_flows",
    "revision_propagation", "exact_evidence_policy", "top6_tie_breaker",
})


class _Missing(ValueError):
    pass


class _Contract(ValueError):
    pass


def _d(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise _Missing("missing or non-numeric dependency")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise _Missing("invalid numeric dependency") from exc
    if not result.is_finite():
        raise _Missing("non-finite dependency")
    return result


def _v(inputs: Mapping[str, Any], key: str) -> Decimal:
    return _d(inputs.get(key))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator > 0 else None


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise _Missing("missing timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Missing("invalid timestamp") from exc
    if result.tzinfo is None:
        raise _Missing("timezone required")
    return result.astimezone(timezone.utc)


def _scoped(inputs: Mapping[str, Any], key: str,
            participates: Callable[[Mapping[str, Any]], bool] | None = None) -> list[Mapping[str, Any]]:
    values = inputs.get(key)
    if not isinstance(values, list):
        raise _Missing("missing source collection")
    window = inputs.get("window")
    output = []
    for event in values:
        if not isinstance(event, Mapping):
            raise _Missing("invalid source row")
        if participates is not None and not participates(event):
            continue
        scope_fields = ("account_id", "strategy_id", "source_system", "exchange", "environment")
        if any(inputs.get(field) is not None and field not in event for field in scope_fields):
            raise _Missing("source row is missing a requested scope identity")
        if any(inputs.get(field) is not None and event.get(field) != inputs[field] for field in scope_fields):
            continue
        if window:
            stamp = _time(event.get("timestamp"))
            if not (_time(window["from"]) <= stamp < _time(window["to"])):
                continue
        output.append(event)
    return output


def _account_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in ("source_system", "exchange", "environment", "account_id"))


def _currency_rows(p: Mapping[str, Any], rows: list[Mapping[str, Any]], *,
                   reject_unconverted: bool = False) -> list[Mapping[str, Any]]:
    """Filter a declared denomination; never infer FX conversion or unknown units."""
    target = p.get("currency")
    currencies = {row.get("currency") for row in rows}
    if target is None and currencies <= {None}:
        return rows  # Legacy fixtures explicitly use one common logical amount unit.
    if None in currencies:
        raise _Missing("one amount has an unknown currency")
    if target is not None:
        return [row for row in rows if row["currency"] == target]
    if len(currencies) > 1:
        if reject_unconverted:
            raise _Missing("unconverted amounts have no common currency")
        raise _Contract("cross-currency summation requires an approved FX rule")
    return rows


def _unique(rows: list[Mapping[str, Any]], key: str = "id") -> list[Mapping[str, Any]]:
    """Exact replay is idempotent; conflicting duplicate events need a contract."""
    output: dict[Any, Mapping[str, Any]] = {}
    for row in rows:
        if row.get(key) is None:
            raise _Missing("source identity required")
        identity = (*_account_key(row), row[key])
        if identity in output and row != output[identity]:
            raise _Contract("conflicting source-event revisions")
        output[identity] = row
    return list(output.values())


def _latest(rows: list[Mapping[str, Any]], key: str,
            local_fields: tuple[str, ...] = ()) -> list[Mapping[str, Any]]:
    output: dict[Any, Mapping[str, Any]] = {}
    for row in rows:
        if row.get(key) is None:
            raise _Missing("source identity required")
        identity = (*_account_key(row), *(row.get(field) for field in local_fields), row[key])
        stamp = _time(row.get("timestamp"))
        previous = output.get(identity)
        if previous is None or stamp > _time(previous["timestamp"]):
            output[identity] = row
        elif stamp == _time(previous["timestamp"]) and previous != row:
            raise _Contract("same-time conflict needs revision precedence")
    return list(output.values())


def _path(inputs: Mapping[str, Any]) -> list[Decimal | None]:
    if "series" in inputs:
        rows = _scoped(inputs, "series")
        points: dict[datetime, Mapping[str, Any]] = {}
        for row in rows:
            if not row.get("closed", True):
                continue
            stamp = _time(row.get("timestamp"))
            if stamp in points and points[stamp] != row:
                raise _Contract("conflicting equity at one timestamp")
            points[stamp] = row
        raw = [row.get("equity") for _, row in sorted(points.items())]
    else:
        raw = inputs.get("equity_path")
    if not isinstance(raw, list) or not raw:
        raise _Missing("missing equity history")
    return [_d(value) if value is not None else None for value in raw]


def _daily(inputs: Mapping[str, Any]) -> list[Decimal | None]:
    if inputs.get("has_cash_flows"):
        raise _Contract("cash-flow-adjusted daily-return rule is unconfirmed")
    path = _path(inputs)
    if "series" in inputs:
        stamps = sorted({_time(row.get("timestamp")) for row in _scoped(inputs, "series") if row.get("closed", True)})
        if any((right - left).total_seconds() != 86400 for left, right in zip(stamps, stamps[1:])):
            profile = inputs.get("daily_boundary_profile")
            if isinstance(profile, Mapping) and profile.get("confirmed") is True and profile.get("cadence_seconds") == 86400:
                raise _Missing("a required boundary is missing from the confirmed daily grid")
            raise _Contract("non-adjacent daily buckets need a confirmed day-boundary/gap rule")
    if len(path) < 2:
        raise _Missing("insufficient daily equity history")
    return [None if a is None or b is None or a <= 0 else b / a - 1
            for a, b in zip(path, path[1:])]


def _volatility(inputs: Mapping[str, Any]) -> tuple[Decimal | None, int]:
    if "returns" in inputs:
        raw = inputs["returns"]
        if not isinstance(raw, list):
            raise _Missing("missing daily returns")
        values = [_d(value) if value is not None else None for value in raw]
    else:
        # An explicitly empty source history proves zero observations. A missing
        # collection remains unknown and follows the normal missing-data guard.
        if inputs.get("equity_path") == [] or inputs.get("series") == []:
            return None, 0
        values = _daily(inputs)
    if len(values) < 2 or any(value is None for value in values):
        return None, len(values)
    valid = [value for value in values if value is not None]
    mean = sum(valid, Decimal(0)) / len(valid)
    variance = sum(((value - mean) ** 2 for value in valid), Decimal(0)) / (len(valid) - 1)
    return (variance * 365).sqrt(), len(valid)


def _same_windows(inputs: Mapping[str, Any], *names: str) -> None:
    windows = inputs.get("calculation_windows")
    if windows is None:
        return
    if not isinstance(windows, Mapping) or any(name not in windows for name in names):
        raise _Missing("each combined measure needs its own declared calculation window")
    bounds = [(_time(windows[name].get("from")), _time(windows[name].get("to"))) for name in names]
    if any(start >= end for start, end in bounds) or len(set(bounds)) != 1:
        raise _Missing("numerator, runtime days and risk denominator are not from the same window")


def _annualized(inputs: Mapping[str, Any]) -> Decimal | None:
    basis = inputs.get("return_basis", "account_cumulative")
    if "return_basis" in inputs and inputs.get("profile_confirmed") is not True:
        raise _Contract("return basis and runtime-day profile must be explicitly confirmed")
    _same_windows(inputs, "return", "days")
    if basis == "account_cumulative":
        rate = _ratio(_v(inputs, "G"), _v(inputs, "D"))
    elif basis == "post_clear_return":
        rate = _ratio(_v(inputs, "G") - _v(inputs, "Gclear"), _v(inputs, "D") + _v(inputs, "Gclear"))
    else:
        raise _Contract("unapproved annualized-return basis")
    days = _v(inputs, "days")
    return rate / days * 365 if rate is not None and days >= 7 else None


def _drawdown(inputs: Mapping[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    if inputs.get("history_complete") is False:
        return None, None
    path = _path(inputs)
    if any(value is None for value in path) or path[0] <= 0:
        return None, None
    peak = path[0]
    maximum = current = Decimal(0)
    for value in path:
        peak = max(peak, value)
        current = (peak - value) / peak
        maximum = max(maximum, current)
    return current, maximum


def _result(**values: Any) -> dict[str, Any]:
    def encode(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {key: encode(item) for key, item in value.items()}
        return value
    return {"status": "unavailable" if any(value is None for value in values.values()) else "ok",
            **{key: encode(value) for key, value in values.items()}}


def _receipt_metrics(p: Mapping[str, Any], bill_ids: set[str] | None) -> tuple[Decimal, str | None]:
    scoped = _scoped(p, "receipts", participates=lambda row: row.get("status") == "confirmed"
                     and (bill_ids is None or row.get("bill_id") in bill_ids))
    if p.get("confirmation_cutoff") is not None:
        scoped = [row for row in scoped if _time(row.get("confirmation_at")) <= _time(p["confirmation_cutoff"])]
    confirmed = _unique(_currency_rows(p, scoped))
    for row in confirmed:
        if _v(row, "amount") < 0:
            raise _Contract("receipt refunds and reversals need an approved treatment")
    received = sum((_v(row, "amount") for row in confirmed), Decimal(0))
    stamps = [_time(row.get("actual_received_at")) for row in confirmed]
    latest = max(stamps).isoformat().replace("+00:00", "Z") if stamps else None
    return received, latest


def _calculate(family: str, p: Mapping[str, Any]) -> dict[str, Any]:
    if family == "net_deposit_change":
        from service.strategy_dashboard_capital_source_flow import net_deposit_change
        return net_deposit_change(p)
    if family == "receipt_registration":
        if p.get("implementation_profile") != "bill-detail-registration-observed-v1":
            raise _Contract("receipt registration profile must be explicit")
        from service.strategy_dashboard_receipt_source_flow import receipt_registration_statistics
        return receipt_registration_statistics(p)
    if family == "ledger_collection":
        # Billed ledger amounts are inputs here, not proof that billing or receipt
        # confirmation computed them. Never derive these inputs from test expected.
        bills = _unique(_scoped(p, "bills"))
        receivable = sum((_v(row, "receivable") for row in bills), Decimal(0))
        received = sum((_v(row, "received") for row in bills), Decimal(0))
        return _result(bill_count=len(bills), receivable_amount=receivable,
                       received_amount=received, outstanding_amount=receivable - received,
                       collection_progress=_ratio(received, receivable))
    if family == "wallet":
        return _result(wallet=_v(p, "E") - _v(p, "U"))
    if family == "net_deposits":
        total = Decimal(0)
        rows = _scoped(p, "events", participates=lambda row: row.get("qualified") is True)
        for row in _unique(_currency_rows(p, rows, reject_unconverted=True)):
            amount = _v(row, "amount")
            if amount < 0 or row.get("direction") not in {"in", "out"}:
                raise _Missing("qualified cash flow requires a direction and nonnegative amount")
            total += amount if row["direction"] == "in" else -amount
        return _result(net_deposit=total)
    if family == "cumulative_return":
        if "accounts" in p:
            raise _Contract("multi-account return aggregation has no approved formula")
        g, d = _v(p, "G"), _v(p, "D")
        return _result(cumulative_return=_ratio(g, d))
    if family == "interval_profit":
        if p.get("counter_reset") is True:
            raise _Missing("cumulative counters are not comparable across a reset")
        if "events" in p:
            totals = {name: Decimal(0) for name in ("G", "C", "F")}
            rows = [row for row in _scoped(p, "events") if row.get("kind") in totals]
            for row in _unique(_currency_rows(p, rows)):
                if row.get("kind") in totals:
                    totals[row["kind"]] += _v(row, "amount")
            value = totals["G"] - totals["C"] + totals["F"]
        else:
            start, end = p["start"], p["end"]
            if len(_currency_rows(p, [start, end], reject_unconverted=True)) != 2:
                raise _Missing("interval endpoints must be in the selected currency")
            value = (_v(end, "G") - _v(start, "G")
                     - (_v(end, "C") - _v(start, "C"))
                     + (_v(end, "F") - _v(start, "F")))
        return _result(interval_net_profit=value)
    if family == "post_clear_return":
        return _result(post_clear_return=_ratio(_v(p, "G") - _v(p, "Gclear"),
                                                _v(p, "D") + _v(p, "Gclear")))
    if family == "billing_principal":
        return _result(billing_principal=_v(p, "D") + _v(p, "Gclear"))
    if family == "billing_profit":
        return _result(billing_profit=_v(p, "G") - _v(p, "Gbase"))
    if family == "billing_return":
        return _result(billing_return=_ratio(_v(p, "G") - _v(p, "Gbase"),
                                            _v(p, "D") + _v(p, "Gclear")))
    if family == "annualized_return":
        return _result(annualized_return=_annualized(p))
    if family == "daily_returns":
        values = _daily(p)
        result = _result(daily_returns=values)
        if any(value is None for value in values):
            result["status"] = "unavailable"
        return result
    if family == "annualized_volatility":
        vol, count = _volatility(p)
        return _result(annualized_volatility=vol, sample_count=count)
    if family == "sharpe":
        _same_windows(p, "return", "days", "volatility")
        vol, _ = _volatility(p)
        annual = _annualized(p)
        return _result(sharpe=annual / vol if annual is not None and vol is not None and vol > 0 else None)
    if family == "calmar":
        _same_windows(p, "return", "days", "drawdown")
        _, maximum = _drawdown(p)
        annual = _annualized(p)
        return _result(calmar=annual / abs(maximum) if annual is not None and maximum else None)
    if family == "drawdown":
        current, maximum = _drawdown(p)
        return _result(current_drawdown=current, max_drawdown=maximum)
    if family == "nav":
        cumulative = cycle = None
        try:
            ratio = _ratio(_v(p, "G"), _v(p, "D"))
            cumulative = 1 + ratio if ratio is not None else None
        except _Missing:
            pass
        try:
            ratio = _ratio(_v(p, "G") - _v(p, "Gclear"), _v(p, "D") + _v(p, "Gclear"))
            cycle = 1 + ratio if ratio is not None else None
        except _Missing:
            pass
        return _result(cumulative_nav=cumulative, cycle_nav=cycle)
    if family == "contribution":
        # Logical rows are already attributed per-strategy results in one declared
        # scope/window. This oracle checks their contribution ratios, not the
        # upstream trade-to-strategy attribution or cross-account aggregation.
        if p.get("collection_complete") is False:
            raise _Missing("the selected strategy collection is incomplete")
        rows = _scoped(p, "strategies")
        if "expected_strategy_ids" in p:
            selected = set(p["expected_strategy_ids"])
            rows = [row for row in rows if row.get("id") in selected]
            if {row.get("id") for row in rows} != selected:
                raise _Missing("one or more selected strategies are completely absent")
        rows = _unique(_currency_rows(p, rows))
        values = [(row["id"], _v(row, "profit")) for row in rows]
        denominator = sum((abs(profit) for _, profit in values), Decimal(0))
        return _result(denominator=denominator, contributions=[
            {"id": identity, "profit": value, "contribution": value / denominator}
            for identity, value in values] if denominator else None)
    if family == "plan_match":
        rows = _latest(_scoped(p, "targets"), "target_id", local_fields=("strategy_id",))
        if any(row.get("status") in {"cancelled", "zero_target"} and "eligible" not in row for row in rows):
            raise _Contract("cancelled/zero-target eligibility rule is unconfirmed")
        valid = [row for row in rows if row.get("eligible", True)]
        if any(type(row.get("matched")) is not bool for row in valid):
            raise _Missing("missing target match outcome")
        matched = sum(row["matched"] for row in valid)
        return _result(target_count=len(valid), matched_count=matched,
                       match_rate=Decimal(matched) / len(valid) if valid else None)
    if family == "order_completion":
        # Logical order_id and target_id are strategy-local identifiers; exchange
        # trade IDs and physical position identities deliberately use other scopes.
        rows = _latest(_scoped(p, "orders"), "order_id", local_fields=("strategy_id",))
        # These are normalized fixture outcomes, not a guessed exchange enum.
        # An adapter must map a supported source status before this calculation;
        # a new or unknown state is not automatically a known incomplete order.
        known_states = {"open", "partial", "filled", "cancelled"}
        if any(not isinstance(row.get("status"), str) or row["status"] not in known_states for row in rows):
            raise _Missing("missing or unmapped latest order outcome")
        completed = sum(row["status"] == "filled" for row in rows)
        return _result(order_count=len(rows), completed_count=completed,
                       completion_rate=Decimal(completed) / len(rows) if rows else None)
    if family == "weighted_fill_price":
        rows = [row for row in _scoped(p, "fills") if row.get("valid", True)]
        if p.get("order_id") is not None:
            if any(row.get("order_id") is None for row in rows):
                raise _Missing("fill order identity required for a selected order")
            rows = [row for row in rows if row["order_id"] == p["order_id"]]
        rows = _unique(_currency_rows(p, rows))
        if len({(*_account_key(row), row.get("order_id")) for row in rows}) > 1:
            raise _Missing("weighted fill price requires one physical account and one order")
        if any(row.get("order_id") is not None for row in rows) and any(row.get("order_id") is None for row in rows):
            raise _Missing("partially missing fill order identity")
        if len({(row.get("symbol"), row.get("quantity_unit"), row.get("price_unit")) for row in rows}) > 1:
            raise _Missing("incompatible fill contracts or units")
        if any(_v(row, "quantity") < 0 for row in rows):
            raise _Missing("fill quantity must be nonnegative")
        quantity = sum((_v(row, "quantity") for row in rows), Decimal(0))
        cost = sum((_v(row, "quantity") * _v(row, "price") for row in rows), Decimal(0))
        return _result(weighted_average_price=_ratio(cost, quantity))
    if family == "positions":
        source = _currency_rows(p, _scoped(p, "positions"), reject_unconverted=True)
        rows = []
        for row in source:
            if any(row.get(key) is None for key in ("account_id", "symbol", "side")):
                raise _Missing("position identity required")
            rows.append({**row, "position_key": (row.get("market"), row.get("position_id"), row["symbol"], row["side"])})
        active = [row for row in _latest(rows, "position_key") if _v(row, "quantity") != 0]
        try:
            gross = sum((abs(_v(row, "notional")) for row in active), Decimal(0))
        except _Missing:
            gross = None
        return _result(position_count=len(active), gross_notional=gross)
    if family == "execution_latency":
        rows = [row for row in _scoped(p, "events") if row.get("chain_id") == p.get("chain_id")]
        triggered = [_time(row.get("timestamp")) for row in rows if row.get("kind") == "trigger"]
        completed = [_time(row.get("timestamp")) for row in rows if row.get("kind") == "completed"]
        if len(set(triggered)) != 1 or not completed:
            raise _Missing("one trigger and completion from the same chain required")
        delta = max(completed) - triggered[0]
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
        return _result(latency_seconds=seconds if seconds >= 0 else None)
    if family == "execution_latest_times":
        rows = _scoped(p, "events", participates=lambda row: row.get("kind") in {"trigger", "completed"})
        latest: dict[str, str | None] = {}
        for kind, field in (("trigger", "latest_triggered_at"), ("completed", "latest_completed_at")):
            stamps = {_time(row.get("timestamp")) for row in rows if row["kind"] == kind}
            latest[field] = max(stamps).isoformat().replace("+00:00", "Z") if stamps else None
        # These independent maxima may belong to different chains and must never
        # be subtracted to manufacture an execution latency.
        return {"status": "ok", **latest}
    if family == "receivable":
        amount = (_v(p, "G") - _v(p, "Gbase")) * _v(p, "share_rate")
        return _result(receivable=amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))
    if family == "billing_gate":
        principal = _v(p, "D") + _v(p, "Gclear")
        profit = _v(p, "G") - _v(p, "Gbase")
        rate = _ratio(profit, principal)
        gate = _v(p, "threshold")
        # This switch is the account's profit-sharing policy, never its trading
        # process status. account_enabled remains a legacy logical-fixture alias.
        policy_enabled = p.get("policy_enabled", p.get("account_enabled"))
        eligible = (policy_enabled is True and p.get("evidence_qualified") is True
                    and principal > 0 and profit > 0 and rate is not None and rate >= gate)
        return _result(eligible=eligible, billing_principal=principal, billing_profit=profit, billing_return=rate)
    if family == "receipts":
        bill_ids = set(p["bill_ids"]) if "bill_ids" in p else None
        received, latest = _receipt_metrics(p, bill_ids)
        if received > _v(p, "receivable"):
            raise _Contract("overpayment treatment is unconfirmed")
        # No confirmed receipts is a known empty set, not a missing source.
        return {"status": "ok", "received": str(received),
                "outstanding": str(_v(p, "receivable") - received), "latest_received_at": latest}
    if family == "bill_summary":
        selected = set(p["bill_ids"]) if "bill_ids" in p else None
        bills = _scoped(p, "bills")
        if selected is not None:
            bills = [bill for bill in bills if bill.get("id") in selected]
        bills = _unique(_currency_rows(p, bills))
        if selected is not None:
            if {bill["id"] for bill in bills} != selected:
                raise _Missing("selected bill collection is incomplete")
        total = sum((_v(bill, "receivable") for bill in bills), Decimal(0))
        receipt_scope = p
        if p.get("currency") is None and bills and bills[0].get("currency") is not None:
            receipt_scope = {**p, "currency": bills[0]["currency"]}
        if any(_receipt_metrics(receipt_scope, {bill["id"]})[0] > _v(bill, "receivable") for bill in bills):
            raise _Contract("one bill is overpaid even when another bill is unpaid")
        received, latest = _receipt_metrics(receipt_scope, {bill["id"] for bill in bills})
        if received > total:
            raise _Contract("overpayment treatment is unconfirmed")
        return {"status": "ok" if total > 0 else "unavailable", "bill_count": len(bills),
                "total_receivable": str(total), "received": str(received), "outstanding": str(total - received),
                "collection_progress": str(received / total) if total > 0 else None,
                "latest_received_at": latest}
    if family == "account_totals":
        fields = p.get("amount_fields", ["equity"])
        supported = {"equity", "wallet", "unrealized_pnl", "realized_pnl", "fees", "funding"}
        if not isinstance(fields, list) or not fields or not set(fields) <= supported:
            raise _Contract("requested account amount fields must have approved definitions")
        rows = _scoped(p, "accounts")
        if "expected_account_ids" in p:
            rows = [row for row in rows if row.get("account_id") in set(p["expected_account_ids"])]
        rows = _currency_rows(p, rows)
        if p.get("cutoff") is not None:
            rows = [row for row in rows if _time(row.get("timestamp")) <= _time(p["cutoff"])]
        values: dict[Any, dict[str, Decimal | None]] = {}
        timestamps: dict[Any, datetime] = {}
        for row in rows:
            if row.get("account_id") is None:
                raise _Missing("physical account identity required")
            identity = _account_key(row)
            parsed: dict[str, Decimal | None] = {}
            for field in fields:
                try:
                    parsed[field] = _v(row, field)
                except _Missing:
                    parsed[field] = None
            if p.get("cutoff") is not None:
                stamp = _time(row.get("timestamp"))
                if identity in timestamps and stamp < timestamps[identity]:
                    continue
                if identity not in timestamps or stamp > timestamps[identity]:
                    timestamps[identity], values[identity] = stamp, parsed
                    continue
            if identity in values and values[identity] != parsed:
                raise _Contract("different snapshots for one physical account")
            values[identity] = parsed
        complete = p.get("collection_complete", True) is True
        if "expected_account_ids" in p:
            complete = complete and {row.get("account_id") for row in rows} == set(p["expected_account_ids"])
        totals = {f"total_{field}": sum((row[field] for row in values.values()), Decimal(0))
                  if complete and all(row[field] is not None for row in values.values()) else None
                  for field in fields}
        return _result(account_count=len(values), **totals)
    if family == "position_totals":
        rows = []
        for row in _currency_rows(p, _scoped(p, "positions")):
            if any(row.get(key) is None for key in ("account_id", "symbol", "side")):
                raise _Missing("position identity required")
            rows.append({**row, "position_key": (row.get("market"), row.get("position_id"), row["symbol"], row["side"])})
        rows = _latest(rows, "position_key")
        unrealized = margin = None
        try:
            unrealized = sum((_v(row, "unrealized_pnl") for row in rows), Decimal(0))
        except _Missing:
            pass
        if rows and (any(row.get("margin_type") is None for row in rows)
                     or len({row.get("margin_type") for row in rows}) > 1):
            return _result(unrealized_pnl=unrealized, used_margin=None)
        try:
            margin = sum((_v(row, "used_margin") for row in rows), Decimal(0))
        except _Missing:
            pass
        return _result(unrealized_pnl=unrealized, used_margin=margin)
    if family == "health_weighted_scores":
        if p.get("approved_profile") is not True:
            raise _Contract("component-score mapping profile is unapproved")
        weights = {"drawdown_score": Decimal("0.4"), "net_exposure_score": Decimal("0.2"),
                   "margin_score": Decimal("0.2"), "match_score": Decimal("0.2")}
        scores = {key: _v(p, key) for key in weights}
        if any(value < 0 or value > 100 for value in scores.values()):
            raise _Missing("component scores must already be approved scores in [0,100]")
        return _result(health_score=sum((scores[key] * weight for key, weight in weights.items()), Decimal(0)))
    if family == "overdue":
        if p.get("deadline_rule_approved") is not True:
            raise _Contract("deadline rule is unconfirmed")
        overdue = (p.get("deadline") is not None and _v(p, "outstanding") > 0
                   and _time(p.get("now")) > _time(p["deadline"]))
        return _result(overdue=overdue)
    raise ValueError(f"Unknown formula family: {family}")


def evaluate_formula(family: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate a documented formula from logical fixture inputs.

    Returns JSON-compatible Decimal strings, null for missing dependencies, and
    ``blocked_contract`` for unapproved rules. Unknown families raise ValueError;
    malformed/missing numeric inputs never become zero or non-finite outputs.
    This function performs no HTTP, DB, file access, or modification of global state.
    Contribution inputs are per-strategy attributed results, not fills; the caller
    must establish that attribution layer and its complete selected strategy scope.
    """
    if family in _UNCONFIRMED:
        return {"status": "blocked_contract", "reason": "BLOCKED_CONTRACT"}
    if family not in _FIELDS:
        raise ValueError(f"Unknown formula family: {family}")
    with localcontext() as context:
        context.prec = 50
        try:
            return _calculate(family, inputs)
        except _Contract:
            return {"status": "blocked_contract", "reason": "BLOCKED_CONTRACT"}
        except (_Missing, KeyError):
            return {"status": "unavailable", **{key: None for key in _FIELDS[family]}}
