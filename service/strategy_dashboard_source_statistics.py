"""Independent recomputation of real source data and deployed-version statistics.

The daily-nav-statistics-v1 Sortino convention is an observed implementation
profile, not approval of unresolved business requirements. Trade inputs here are
source count/amount aggregates; these checks do not validate event selection.
No expected value is read from a precomputed statistics.value field.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from api.strategy_dashboard_source_statistics_api import SourceStatisticsAPI
from db.strategy_dashboard_source_statistics_repository import SourceStatisticsRepository


def daily_nav_statistics(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute sample volatility and the observed zero-target Sortino convention.

Inputs must be consecutive closed daily NAV boundaries with positive finite
values. Invalid or insufficient series raise ValueError instead of silently
discarding points. A zero downside deviation produces an unavailable Sortino.
"""
    if len(points) < 3:
        raise ValueError("INSUFFICIENT_DAILY_RETURNS")
    times = [datetime.fromisoformat(point["At"].replace("Z", "+00:00")) for point in points]
    if any((right - left).total_seconds() != 86400 for left, right in zip(times, times[1:])):
        raise ValueError("NONCONSECUTIVE_DAILY_NAV")
    with localcontext() as context:
        context.prec = 50
        nav = [Decimal(str(point["Value"])) for point in points]
        if any(not value.is_finite() or value <= 0 for value in nav):
            raise ValueError("NONPOSITIVE_OR_NONFINITE_NAV")
        returns = [right / left - 1 for left, right in zip(nav, nav[1:])]
        count = Decimal(len(returns))
        mean = sum(returns, Decimal(0)) / count
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (count - 1)
        volatility = (variance * 365).sqrt()
        downside = (sum((min(value, Decimal(0)) ** 2 for value in returns), Decimal(0)) / count * 365).sqrt()
        sortino = mean * 365 / downside if downside != 0 else None
        return {"sample_count": len(returns), "volatility": str(volatility),
                "sortino_ratio": None if sortino is None else str(sortino),
                "daily_returns": [str(value) for value in returns],
                "downside_deviation": str(downside), "mean_daily_return": str(mean)}


def realized_income_statistics(trades: dict[str, Any]) -> dict[str, Any]:
    """Recompute ratios from nonzero-event counts and positive gain/loss amounts.

This boundary starts after event classification and excludes break-even events,
as named by realized-income-statistics-v1. Missing/invalid source values raise
ValueError. No-event and no-loss ratios remain unavailable, never infinity.
"""
    counts = [Decimal(str(trades[key])) for key in ("Wins", "Losses")]
    if any(not n.is_finite() or n < 0 or n != n.to_integral_value() for n in counts):
        raise ValueError("INVALID_EVENT_COUNTS")
    with localcontext() as context:
        context.prec = 50
        wins, losses = counts
        gain, loss = (Decimal(str(trades[key])) for key in ("Gain", "Loss"))
        if any(not value.is_finite() or value < 0 for value in (gain, loss)):
            raise ValueError("INVALID_GAIN_LOSS_AMOUNTS")
        if (wins == 0) != (gain == 0) or (losses == 0) != (loss == 0):
            raise ValueError("EVENT_COUNT_AMOUNT_MISMATCH")
        total = wins + losses
        win_rate = wins / total if total else None
        ratio = (gain / wins) / (loss / losses) if wins and losses and loss else None
        return {"sample_count": int(total), "win_rate": None if win_rate is None else str(win_rate),
                "profit_loss_ratio": None if ratio is None else str(ratio)}


def calmar_from_source_samples(samples: list[dict[str, Any]], account_id: str) -> dict[str, Any]:
    """Recompute Calmar from uniquely identified source annual return and max DD.

Annual return/max DD themselves are upstream results, so this verifies only
their final ratio. Ambiguous or mismatched source timestamps are rejected.
"""
    inputs = {}
    for code in ("account_annualized_return", "account_max_drawdown"):
        matches = [row for row in samples if row["account_id"] == account_id and row["metric"] == code]
        if len(matches) != 1:
            raise ValueError("AMBIGUOUS_OR_MISSING_CALMAR_SOURCE")
        inputs[code] = matches[0]
    first, second = inputs.values()
    if first["observed_at"] != second["observed_at"]:
        raise ValueError("CALMAR_SOURCE_WINDOW_MISMATCH")
    with localcontext() as context:
        context.prec = 50
        annual = Decimal(first["value"])
        drawdown = Decimal(second["value"])
        if not annual.is_finite() or not drawdown.is_finite():
            raise ValueError("NONFINITE_CALMAR_INPUT")
        value = annual / abs(drawdown) if drawdown else None
        return {"calmar_ratio": None if value is None else str(value), "inputs": inputs,
                "observed_at": first["observed_at"]}


class SourceStatisticsAudit:
    """Bind live responses to source generations and return independently computed expectations."""

    def __init__(self, repository: SourceStatisticsRepository, api: SourceStatisticsAPI) -> None:
        """Receive independent source reader and deployed API; no I/O at construction."""
        self.repository, self.api = repository, api

    def collect(self) -> list[dict[str, Any]]:
        """Read all mapped natural accounts and recompute each available raw sample.

Source gaps remain explicit per account; HTTP/identity failures raise and must
not be counted as formula passes. Every request resolves the matching batch
after the response, avoiding cross-generation comparisons.
"""
        output = []
        for account in self.repository.mapped_accounts():
            local_id, nt_id = account["account_id"], account["active_nt_account_id"]
            response = self.api.summary(local_id)
            if response["http_status"] != 200 or response["body"].get("success") is not True:
                raise RuntimeError(f"SOURCE_SUMMARY_HTTP_FAILURE: account {local_id}; HTTP {response['http_status']}")
            body = response["body"]
            if str(body["data"]["context"]["subject_id"]) != str(local_id) or body["meta"]["mock"] is not False:
                raise ValueError("SOURCE_SUMMARY_IDENTITY_MISMATCH")
            generation = self.repository.source_at(body["meta"]["fetched_at"])
            source = generation["source"]
            statistics = [row for row in source.get("statistics", []) if row["account_id"] == nt_id]
            record: dict[str, Any] = {"account_id": local_id, "nt_account_id": nt_id,
                                      "batch_id": generation["batch_id"], "fetched_at": body["meta"]["fetched_at"],
                                      "response": response, "expected": {}, "source_gaps": [], "inputs": {}}
            nav_rows = [row for row in statistics if "nav" in row]
            if len(nav_rows) == 1:
                raw_nav = {key: nav_rows[0][key] for key in ("from", "to", "source", "formula", "samples", "nav")}
                record["inputs"]["daily_nav"] = raw_nav
                computed = daily_nav_statistics(raw_nav["nav"])
                for metric in ("sortino_ratio", "volatility"):
                    record["expected"][metric] = {"value": computed[metric], "sample_count": computed["sample_count"],
                                                    "from": raw_nav["from"], "to": raw_nav["to"],
                                                    "formula_version": "daily-nav-statistics-v1"}
                record["daily_calculation"] = computed
            else:
                record["source_gaps"].append("DAILY_NAV_UNAVAILABLE")
            trade_rows = [row for row in statistics if "trades" in row]
            if len(trade_rows) == 1:
                raw_trades = {key: trade_rows[0][key] for key in ("from", "to", "source", "formula", "samples", "trades")}
                record["inputs"]["trade_aggregates"] = raw_trades
                computed = realized_income_statistics(raw_trades["trades"])
                for metric in ("win_rate", "profit_loss_ratio"):
                    record["expected"][metric] = {"value": computed[metric], "sample_count": computed["sample_count"],
                                                    "from": raw_trades["from"], "to": raw_trades["to"],
                                                    "formula_version": "realized-income-statistics-v1"}
            else:
                record["source_gaps"].append("NONZERO_REALIZED_EVENT_AGGREGATES_UNAVAILABLE")
            try:
                computed = calmar_from_source_samples(source["raw"]["samples"], nt_id)
                record["inputs"]["calmar"] = computed["inputs"]
                record["expected"]["calmar_ratio"] = {"value": computed["calmar_ratio"],
                                                        "observed_at": computed["observed_at"],
                                                        "formula_version": "prometheus-calmar-v1"}
            except ValueError as error:
                record["source_gaps"].append(str(error))
            output.append(record)
        return output
