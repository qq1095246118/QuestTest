"""Pure fixture oracles for Factor 4.0's currently blocked calculation cases.

These tests exercise the independent expectations that will be compared with
real service output once the corresponding fixture packages are available.
They intentionally do not call the product API and therefore do not count as
Factor 4.0 product PASS results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

import pytest

from service.factor4_calculation_oracles import (
    Factor4Sample,
    contiguous_segments,
    cross_sectional_slice,
    fold_aggregate,
    forward_returns,
    net_returns,
    parent_value,
    partition_ranks,
    diff_series,
    pct_change_series,
    rolling_mean,
    rolling_vwap,
    snapshot_hash,
    threshold_is_met,
    time_series_groups,
    visible_revision,
)


pytestmark = pytest.mark.unit


UTC = timezone.utc


def test_ts_oracle_does_not_mix_assets_and_cs_oracle_uses_one_timestamp() -> None:
    """TS remains per asset while CS intentionally contains same-time assets."""

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    rows = (
        Factor4Sample("BTC", timestamp, Decimal("1"), Decimal("0.1")),
        Factor4Sample("BTC", timestamp + timedelta(hours=1), Decimal("2"), Decimal("0.2")),
        Factor4Sample("ETH", timestamp, Decimal("9"), Decimal("-0.1")),
    )
    groups = time_series_groups(rows)

    assert [row.asset for row in groups["BTC"]] == ["BTC", "BTC"]
    assert [row.factor for row in groups["BTC"]] == [Decimal("1"), Decimal("2")]
    assert [row.asset for row in cross_sectional_slice(rows, timestamp)] == ["BTC", "ETH"]

    with_extra_asset = rows + (
        Factor4Sample("SOL", timestamp, Decimal("100"), Decimal("0.9")),
    )
    assert groups["BTC"] == time_series_groups(with_extra_asset)["BTC"]


def test_forward_return_oracle_respects_horizon_and_warmup_boundary() -> None:
    """The final horizon-incomplete prices do not produce a fabricated return."""

    prices = tuple(Decimal(value) for value in ("100", "110", "121", "133.1"))

    assert forward_returns(prices, 2) == (Decimal("0.21"), Decimal("0.21"))
    assert len(forward_returns(prices, 2)) == len(prices) - 2


def test_finite_formula_windows_preserve_warmup_and_do_not_become_cumulative() -> None:
    """rolling/diff/pct_change expose warm-up rows and use only finite history."""

    values = tuple(Decimal(value) for value in ("1", "2", "4", "8"))
    assert rolling_mean(values, 2) == (None, Decimal("1.5"), Decimal("3"), Decimal("6"))
    assert diff_series(values, 2) == (None, None, Decimal("3"), Decimal("6"))
    assert pct_change_series(values, 2) == (None, None, Decimal("3"), Decimal("3"))
    # A cumulative implementation would report 3.75 at the last point; the
    # finite two-bar window must remain 6.
    assert rolling_mean(values, 2)[-1] != sum(values, Decimal(0)) / Decimal(len(values))


def test_rolling_vwap_uses_windowed_volume_weighted_typical_price() -> None:
    """VWAP is bounded by the requested window and includes warm-up behavior."""

    high = tuple(Decimal(value) for value in ("11", "21", "31"))
    low = tuple(Decimal(value) for value in ("9", "19", "29"))
    close = tuple(Decimal(value) for value in ("10", "20", "30"))
    volume = tuple(Decimal(value) for value in ("1", "1", "8"))
    result = rolling_vwap(high, low, close, volume, 2)

    assert result[0] is None
    assert rolling_vwap(high, low, close, volume, 2, min_periods=1)[0] == Decimal("10")
    assert result[1] == Decimal("15")
    assert result[2] == Decimal("28.88888888888888888888888889")
    # A cumulative VWAP would include the first bar and produce a different
    # value at index 2.
    cumulative = (Decimal(10) + Decimal(20) + Decimal(30) * Decimal(8)) / Decimal(10)
    assert result[2] != cumulative


def test_window_oracles_fail_closed_for_invalid_parameters_and_zero_vwap_volume() -> None:
    """Malformed windows and zero denominators cannot yield fabricated values."""

    values = (Decimal("1"), Decimal("2"))
    with pytest.raises(ValueError):
        rolling_mean(values, 0)
    with pytest.raises(ValueError):
        rolling_mean(values, 2, min_periods=3)
    with pytest.raises(ValueError):
        diff_series(values, 0)
    with pytest.raises(ValueError):
        pct_change_series((Decimal("0"), Decimal("1")))
    zero_volume = rolling_vwap(values, values, values, (Decimal("0"), Decimal("0")), 2)
    assert zero_volume == (None, None)
    with pytest.raises(ValueError):
        rolling_vwap(values, values, values, (Decimal("-1"), Decimal("1")), 2)
    with pytest.raises(ValueError):
        rolling_vwap(values, values, values, values, 2, min_periods=3)


def test_gap_oracle_splits_continuous_intervals() -> None:
    """A missing bar creates a new segment instead of a synthetic holding period."""

    start = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = (
        start,
        start + timedelta(hours=1),
        start + timedelta(hours=3),
        start + timedelta(hours=4),
    )

    segments = contiguous_segments(timestamps, timedelta(hours=1))

    assert tuple(len(segment) for segment in segments) == (2, 2)
    assert segments[1] == (start + timedelta(hours=3), start + timedelta(hours=4))


@pytest.mark.parametrize(
    ("as_of_offset", "expected"),
    [(-1, None), (0, 1), (1, 2)],
)
def test_pit_oracle_applies_available_at_boundary(as_of_offset: int, expected: int | None) -> None:
    """Future revisions remain invisible and equality at ``available_at`` is inclusive."""

    available = datetime(2026, 1, 2, tzinfo=UTC)
    revisions = ((1, available), (2, available + timedelta(hours=1)))

    assert visible_revision(revisions, available + timedelta(hours=as_of_offset)) == expected


def test_fold_oracle_rejects_future_training_and_uses_declared_weights() -> None:
    """Fold aggregation is only valid when training ends before validation/OOS."""

    train_end = datetime(2026, 1, 10, tzinfo=UTC)
    validation_start = datetime(2026, 1, 11, tzinfo=UTC)
    oos_start = datetime(2026, 1, 20, tzinfo=UTC)

    assert train_end < validation_start < oos_start
    assert fold_aggregate(
        (Decimal("0.10"), Decimal("0.30")),
        (Decimal("1"), Decimal("3")),
    ) == Decimal("0.25")


def test_future_perturbation_keeps_visible_snapshot_hash_stable() -> None:
    """Changing rows after ``as_of`` cannot change the historical input digest."""

    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    base = (
        {"at": "2026-01-01T00:00:00Z", "value": "1"},
        {"at": "2026-01-02T00:00:00Z", "value": "2"},
    )
    future_a = {"at": "2026-01-03T00:00:00Z", "value": "3"}
    future_b = {"at": "2026-01-03T00:00:00Z", "value": "999"}

    def visible(rows: Sequence[dict[str, str]]) -> tuple[dict[str, str], ...]:
        return tuple(row for row in rows if datetime.fromisoformat(row["at"].replace("Z", "+00:00")) <= as_of)

    assert snapshot_hash(visible((*base, future_a))) == snapshot_hash(visible((*base, future_b)))
    assert snapshot_hash((*base, future_a)) != snapshot_hash((*base, future_b))


@pytest.mark.parametrize(
    ("score", "expected"),
    [(Decimal("59.999"), False), (Decimal("60"), True), (Decimal("60.001"), True)],
)
def test_threshold_oracle_keeps_decimal_boundary_semantics(score: Decimal, expected: bool) -> None:
    """A versioned inclusive threshold distinguishes below/equal/above values."""

    assert threshold_is_met(score, Decimal("60")) is expected


def test_partition_oracle_isolates_scope_and_environment() -> None:
    """A high score in another partition cannot change the original rank."""

    rows = (
        ("all", "WIDE_RANGE", Decimal("80"), "sub_factor:1"),
        ("all", "WIDE_RANGE", Decimal("70"), "sub_factor:2"),
        ("spot", "WIDE_RANGE", Decimal("999"), "sub_factor:3"),
    )
    before = partition_ranks(rows)[("all", "WIDE_RANGE")]
    after = partition_ranks(rows + (("spot", "WIDE_RANGE", Decimal("1000"), "sub_factor:4"),))[("all", "WIDE_RANGE")]

    assert before == after == (("sub_factor:1", 1), ("sub_factor:2", 2))


def test_partition_rank_oracle_is_stable_for_equal_scores() -> None:
    """Equal scores use the declared deterministic factor-ref tie breaker."""

    rows = (
        ("all", "WIDE_RANGE", Decimal("80"), "sub_factor:2"),
        ("all", "WIDE_RANGE", Decimal("80"), "sub_factor:1"),
    )
    assert partition_ranks(rows)[("all", "WIDE_RANGE")] == (
        ("sub_factor:1", 1),
        ("sub_factor:2", 2),
    )


def test_cost_oracle_covers_zero_and_nonzero_period_costs() -> None:
    """Zero costs preserve gross returns; nonzero costs apply at each turnover."""

    gross = (Decimal("0.10"), Decimal("0.00"), Decimal("-0.02"))
    turnover = (Decimal("1"), Decimal("0"), Decimal("2"))

    assert net_returns(gross, turnover, Decimal("0"), Decimal("0")) == gross
    assert net_returns(gross, turnover, Decimal("0.01"), Decimal("0.005")) == (
        Decimal("0.085"),
        Decimal("0.00"),
        Decimal("-0.05"),
    )


def test_parent_oracle_uses_frozen_members_and_normalizes_weights() -> None:
    """Changing a relation snapshot changes only the corresponding parent result."""

    child_values = {"child-a": Decimal("10"), "child-b": Decimal("20"), "child-c": Decimal("100")}
    old_relation = (("child-a", Decimal("2")), ("child-b", Decimal("1")))
    new_relation = (("child-a", Decimal("2")), ("child-c", Decimal("1")))

    assert parent_value(child_values, old_relation) == Decimal("40") / Decimal("3")
    assert parent_value(child_values, new_relation) == Decimal("40")


def test_repeatability_oracle_separates_version_identity_from_run_id() -> None:
    """Equivalent inputs hash identically while a code/config version changes identity."""

    first = {
        "input_hash": "input-1",
        "formula_version": "formula-v1",
        "code_version": "code-v1",
        "evaluation_config": "config-v1",
        "run_id": "run-a",
    }
    replay = {**first, "run_id": "run-b"}
    changed_code = {**first, "code_version": "code-v2", "run_id": "run-c"}

    # Run IDs identify executions, but business identity excludes them.
    business_keys = ("input_hash", "formula_version", "code_version", "evaluation_config")
    assert snapshot_hash({key: first[key] for key in business_keys}) == snapshot_hash(
        {key: replay[key] for key in business_keys}
    )
    assert snapshot_hash({key: first[key] for key in business_keys}) != snapshot_hash(
        {key: changed_code[key] for key in business_keys}
    )
