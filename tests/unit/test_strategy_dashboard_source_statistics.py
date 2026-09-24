"""Numerical golden values and rejection checks for the independent source reader."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from service.strategy_dashboard_source_statistics import daily_nav_statistics, realized_income_statistics

_PROFILES = json.loads((Path(__file__).resolve().parents[1] / "data/strategy_dashboard/source_statistics_profiles.json").read_text())


@pytest.mark.parametrize("case", _PROFILES, ids=lambda case: case["id"])
def test_current_implementation_profile_has_fixed_independent_expected_values(case):
    if case["kind"] == "nav":
        if "expected_error" in case:
            with pytest.raises(ValueError, match=case["expected_error"]):
                daily_nav_statistics(_points(case["values"]))
            return
        actual = daily_nav_statistics(_points(case["values"]))
    else:
        values = [Decimal(value) for value in case["pnl"]]
        actual = realized_income_statistics({"Wins": sum(value > 0 for value in values),
                                             "Losses": sum(value < 0 for value in values),
                                             "Gain": str(sum((value for value in values if value > 0), Decimal(0))),
                                             "Loss": str(-sum((value for value in values if value < 0), Decimal(0)))})
    for field, expected in case["expected"].items():
        if expected is None:
            assert actual[field] is None
        else:
            assert abs(Decimal(actual[field]) - Decimal(expected)) < Decimal("1e-17")


def _points(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"At": (start + timedelta(days=i)).isoformat(), "Value": value} for i, value in enumerate(values)]


def test_daily_statistics_fixed_mixed_returns_use_sample_sd_and_full_downside_population():
    result = daily_nav_statistics(_points([100, 110, 99, 108.9]))
    assert result["sample_count"] == 3
    assert abs(Decimal(result["volatility"]) - Decimal("2.206052281036573")).copy_abs() < Decimal("1e-14")
    assert abs(Decimal(result["sortino_ratio"]) - Decimal("11.030261405182865")) < Decimal("1e-14")


def test_constant_nav_has_zero_volatility_and_unavailable_sortino():
    result = daily_nav_statistics(_points([100, 100, 100]))
    assert Decimal(result["volatility"]) == 0
    assert result["sortino_ratio"] is None


@pytest.mark.parametrize("values", [[100, 90], [100, 0, 90], [100, "NaN", 90], [100, "Infinity", 90]])
def test_invalid_daily_input_is_not_silently_filtered(values):
    with pytest.raises(ValueError):
        daily_nav_statistics(_points(values))


def test_missing_daily_boundary_is_rejected():
    points = _points([100, 90, 95, 100])
    with pytest.raises(ValueError, match="NONCONSECUTIVE"):
        daily_nav_statistics([points[0], points[2], points[3]])


def test_trade_ratio_is_average_gain_over_average_loss_not_total_gain_over_total_loss():
    result = realized_income_statistics({"Wins": 2, "Losses": 1, "Gain": "60", "Loss": "15"})
    assert result["sample_count"] == 3
    assert Decimal(result["profit_loss_ratio"]) == 2
    assert abs(Decimal(result["win_rate"]) - Decimal(2) / 3) < Decimal("1e-27")


@pytest.mark.parametrize("trades", [
    {"Wins": 1.5, "Losses": 1, "Gain": 20, "Loss": 10},
    {"Wins": 0, "Losses": 1, "Gain": 20, "Loss": 10},
    {"Wins": 1, "Losses": 1, "Gain": "NaN", "Loss": 10},
])
def test_invalid_trade_summary_is_rejected(trades):
    with pytest.raises(ValueError):
        realized_income_statistics(trades)
