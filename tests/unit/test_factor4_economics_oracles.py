"""Offline fixtures for Factor 4.0 v2 portfolio/economic rules."""

from decimal import Decimal

import pytest

from service.factor4_calculation_oracles import (
    cross_sectional_positions,
    daily_win_rate,
    equity_curve,
    max_drawdown,
    sharpe_ratio,
    strictly_monotonic,
    time_series_positions,
    turnover_series,
)


pytestmark = pytest.mark.unit
D = Decimal


def test_time_series_positions_clip_direction_and_missing_assets() -> None:
    """TS uses ddof-independent frozen parameters, +/-3 clipping and zero fill."""

    result = time_series_positions(
        {"a": D("10"), "b": D("-10"), "c": None},
        {"a": D("0"), "b": D("0")}, {"a": D("2"), "b": D("2")}, direction=-1, frozen_asset_count=4,
    )
    assert result == {"a": D("-0.25"), "b": D("0.25"), "c": D("0")}
    clipped = time_series_positions({"a": D("100")}, D("0"), D("1"), direction=1, frozen_asset_count=1)
    assert clipped["a"] == D("1")


def test_cross_sectional_positions_use_ceil_twenty_percent_and_gross_two() -> None:
    """Top/bottom tails are selected by stable rank and each side has exposure one."""

    result = cross_sectional_positions({"a": D("1"), "b": D("2"), "c": D("3"), "d": D("4"), "e": D("5"), "f": D("6")})
    assert result["a"] == D("-0.5")
    assert result["f"] == D("0.5")
    assert sum(abs(value) for value in result.values()) == D("2")
    reversed_result = cross_sectional_positions(
        {"a": D("1"), "b": D("2"), "c": D("3"), "d": D("4"), "e": D("5")},
        direction=-1,
    )
    assert reversed_result is not None and reversed_result["a"] == D("1")


def test_turnover_includes_open_rebalance_exit_and_close() -> None:
    """Full-grid turnover includes transitions from and back to zero."""

    grid = ({"a": D("1")}, {"a": D("0.5"), "b": D("0.5")}, {"b": D("0")})
    assert turnover_series(grid) == (D("1"), D("1"), D("1"))


def test_equity_and_drawdown_include_initial_capital_and_bankruptcy_clip() -> None:
    """A return <= -1 bankrupts the curve and drawdown is measured from one."""

    curve = equity_curve((D("0.1"), D("-1"), D("0.2")))
    assert curve == (D("1.1"), D("0"), D("0"))
    assert max_drawdown(curve) == D("1")


def test_sharpe_includes_zero_bars_and_win_rate_uses_active_denominator() -> None:
    """Zero exposure bars remain in Sharpe; inactive days are excluded from win rate."""

    assert sharpe_ratio((D("0"), D("0.1"), D("0")), D("1")) > 0
    assert daily_win_rate(((D("0.1"), True), (D("-0.1"), True), (D("10"), False))) == D("0.5")


@pytest.mark.parametrize("values", [(D("1"), D("2"), D("3")), (D("3"), D("2"), D("1"))])
def test_monotonicity_requires_strict_direction(values: tuple[Decimal, ...]) -> None:
    """Equal adjacent buckets are not monotonic."""

    assert strictly_monotonic(values)
    assert not strictly_monotonic((D("1"), D("1"), D("2")))
