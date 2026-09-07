"""Additional offline boundary oracles for Factor 4.0 calculation checks.

These tests intentionally exercise the small, deterministic helpers used by
``Factor4CalculationService``.  They do not claim that a live database has
the raw bars needed for CALC-501-A/B, CALC-502, CALC-503, or CALC-508; those
sub-items remain fixture-gated in the integration suite.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from service.factor4_calculation_service import (
    FormulaOffsetError,
    _as_of_date,
    _as_string_set,
    _check_partition_ranks,
    _check_route_as_of_contract,
    _is_correct_dpo,
    _is_correct_fixed_horizon,
    _metric_pairs,
    _parse_route_profile_weights,
    _partition_key,
    _plain_int,
    _route_partitions,
    _to_decimal,
    _matches_persisted_scale,
    formula_dependency_offsets,
)
from tests.unit.test_factor4_calculation_service import (
    _metric,
    _route,
    _service,
    _snapshot,
    TestRouteScoreRecalculation,
)


pytestmark = pytest.mark.unit


class TestTemporalBoundaryOracle:
    """Ensure finite-window parsing is exact and fail-closed."""

    @pytest.mark.parametrize(
        "expression",
        (
            "close.rolling(0).mean()",
            "close.rolling(-1).mean()",
            "close.rolling(3.5).mean()",
            "close.rolling(5 / 2).mean()",
            "close.rolling(2 ** 3).mean()",
            "close.shift(periods=True)",
            "close.shift(periods=1.5)",
        ),
    )
    def test_invalid_window_arithmetic_is_blocked(self, expression: str) -> None:
        """零、负数、浮点或非整除算术不能伪装成有限窗口。"""

        with pytest.raises(FormulaOffsetError):
            formula_dependency_offsets(expression)

    @pytest.mark.parametrize(
        ("expression", "expected"),
        (
            ("close.rolling((2 + 4) // 2).mean()", (0, 1, 2)),
            ("close.shift((3 * 2) // 3)", (2,)),
            ("close.pct_change(periods=0)", (0,)),
            ("close.diff(periods=2).rolling(window=2).mean()", (0, 1, 2, 3)),
        ),
    )
    def test_static_integer_arithmetic_and_nested_windows_are_exact(
        self,
        expression: str,
        expected: tuple[int, ...],
    ) -> None:
        assert formula_dependency_offsets(expression) == expected

    @pytest.mark.parametrize(
        ("expression", "window", "expected"),
        (
            (
                "mean(close, WINDOW=60) - close.shift(PERIODS=31)",
                60,
                True,
            ),
            (
                "close.rolling(WINDOW=60).mean() - close.shift(PERIODS=31)",
                60,
                True,
            ),
            (
                "mean(close, WINDOW=48) - close.shift(PERIODS=31)",
                60,
                False,
            ),
        ),
    )
    def test_dpo_predicate_matches_case_insensitive_parser_keywords(
        self,
        expression: str,
        window: int,
        expected: bool,
    ) -> None:
        """回归谓词与 AST parser 对窗口参数使用同一大小写语义。"""

        assert _is_correct_dpo(expression, window) is expected

    @pytest.mark.parametrize(
        ("expression", "family", "period", "window"),
        (
            (
                "funding_rate.diff(PERIODS=12).diff(PERIODS=12)",
                "funding_diff2",
                12,
                24,
            ),
            (
                "long_short_ratio.PCT_CHANGE(PERIODS=48)",
                "long_short_pct",
                48,
                48,
            ),
        ),
    )
    def test_fixed_horizon_predicate_matches_case_insensitive_keywords(
        self,
        expression: str,
        family: str,
        period: int,
        window: int,
    ) -> None:
        assert _is_correct_fixed_horizon(expression, family, period, window)


class TestDecimalAndIdentityBoundaries:
    """Keep malformed numeric and identity values from becoming valid data."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        (
            (None, None),
            (True, None),
            (False, None),
            ("NaN", None),
            ("Infinity", None),
            ("not-a-number", None),
            (" 1.250 ", Decimal("1.250")),
            (2, Decimal("2")),
            (2.5, Decimal("2.5")),
        ),
    )
    def test_to_decimal_rejects_non_finite_and_boolean_values(
        self,
        value: Any,
        expected: Decimal | None,
    ) -> None:
        assert _to_decimal(value) == expected

    def test_persisted_scale_uses_half_up_for_positive_and_negative_values(self) -> None:
        assert _matches_persisted_scale(Decimal("1.2345"), Decimal("1.235"))
        assert _matches_persisted_scale(Decimal("-1.2345"), Decimal("-1.235"))
        assert not _matches_persisted_scale(Decimal("1.2344"), Decimal("1.235"))

    def test_extreme_persisted_exponent_is_a_non_match_not_a_process_error(self) -> None:
        """异常 scale 来自不可信 JSON/DB 时应返回不匹配而不是抛 Decimal 异常。"""

        assert not _matches_persisted_scale(
            Decimal("1"),
            Decimal("1E+1000000"),
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        (
            (1, 1),
            (" +0012 ", 12),
            (Decimal("4"), 4),
            (1.0, 1),
            (True, None),
            (1.5, None),
            ("1.5", None),
            ("NaN", None),
            (None, None),
        ),
    )
    def test_plain_int_never_truncates_fractional_or_boolean_values(
        self,
        value: Any,
        expected: int | None,
    ) -> None:
        assert _plain_int(value) == expected

    @pytest.mark.parametrize(
        "raw",
        (
            {},
            {"time_series": "1"},
            {"time_series": "-1", "cross_sectional": "2"},
            {"time_series": "0", "cross_sectional": "0"},
            {"time_series": "NaN", "cross_sectional": "1"},
            {"time_series": True, "cross_sectional": "1"},
        ),
    )
    def test_route_weights_require_two_finite_non_negative_dimensions(
        self,
        raw: dict[str, Any],
    ) -> None:
        parsed, error = _parse_route_profile_weights(raw)
        assert parsed is None
        assert error

    def test_route_weights_preserve_decimal_values_without_normalizing_them(self) -> None:
        parsed, error = _parse_route_profile_weights(
            {"time_series": "0.1250", "cross_sectional": Decimal("0.8750")}
        )
        assert error is None
        assert parsed == {
            "time_series": Decimal("0.1250"),
            "cross_sectional": Decimal("0.8750"),
        }

    @pytest.mark.parametrize(
        ("value", "expected"),
        (
            (["time_series", "cross_sectional"], {"time_series", "cross_sectional"}),
            (["time_series", "time_series"], {"time_series"}),
            ("time_series", None),
            (["time_series", 1], None),
            (None, None),
        ),
    )
    def test_scope_lists_are_type_checked_before_set_comparison(
        self,
        value: Any,
        expected: set[str] | None,
    ) -> None:
        assert _as_string_set(value) == expected


class TestMetricAndRankBoundaries:
    """Verify pairing, partition filtering, and PIT timestamp assertions."""

    def test_unknown_metric_scope_is_a_failure_and_duplicate_scope_is_blocked(self) -> None:
        ts = _metric(801, "sub_factor:81", "time_series")
        duplicate = _metric(802, "sub_factor:81", "time_series")
        unknown = replace(
            _metric(803, "sub_factor:81", "cross_sectional"),
            evaluation_type="future",
        )
        pairs, issues = _metric_pairs((ts, duplicate, unknown))
        assert pairs
        assert any(issue.code == "METRIC_SCOPE_NOT_UNIQUE" for issue in issues)
        assert any(issue.code == "METRIC_EVALUATION_TYPE_UNKNOWN" for issue in issues)

    def test_partition_filters_inactive_and_ineligible_routes(self) -> None:
        eligible = _route(901, "sub_factor:91", 1001, 1, "90")
        inactive = replace(_route(902, "sub_factor:92", 1002, 2, "80"), is_active=False)
        ineligible = replace(_route(903, "sub_factor:93", 1003, 2, "70"), is_eligible=False)
        partitions = _route_partitions((eligible, inactive, ineligible))
        assert sum(len(rows) for rows in partitions.values()) == 1
        assert next(iter(partitions.values()))[0].factor_ref == "sub_factor:91"

    def test_partition_rank_check_detects_duplicate_factor_version(self) -> None:
        first = _route(904, "sub_factor:94", 1004, 1, "90")
        duplicate_identity = replace(first, id=905, metric_id=1005, rank_no=2, routing_score=Decimal("80"))
        issues: list[Any] = []
        _check_partition_ranks(
            _route_partitions((first, duplicate_identity)),
            issues,
            "unit",
        )
        assert any(issue.code == "RANK_FACTOR_VERSION_DUPLICATE" for issue in issues)

    def test_partition_key_uses_calendar_as_of_date_and_scope_dimensions(self) -> None:
        route = _route(906, "sub_factor:96", 1006, 1, "90")
        key = _partition_key(route)
        assert key[-1] == date(2026, 9, 2)
        changed = replace(route, label_code="NARROW_RANGE")
        assert _partition_key(changed) != key

    @pytest.mark.parametrize(
        ("value", "expected"),
        (
            (datetime(2026, 9, 2, 1, 17), date(2026, 9, 2)),
            (datetime(2026, 9, 2, 1, 17, tzinfo=timezone.utc), date(2026, 9, 2)),
            (date(2026, 9, 2), date(2026, 9, 2)),
            (None, None),
            ("2026-09-02", None),
        ),
    )
    def test_as_of_date_does_not_guess_from_strings(
        self,
        value: Any,
        expected: date | None,
    ) -> None:
        assert _as_of_date(value) == expected

    def test_route_as_of_contract_rejects_same_day_clock_drift(self) -> None:
        route = _route(907, "sub_factor:97", 1007, 1, "90")
        drifted = replace(route, as_of_time=datetime(2026, 9, 2, 2, 17))
        issues: list[Any] = []
        _check_route_as_of_contract(
            datetime(2026, 9, 2, 1, 17),
            (drifted,),
            issues,
            "unit",
        )
        assert any(issue.code == "RANK_ROUTE_AS_OF_TIME_MISMATCH" for issue in issues)

    def test_route_as_of_contract_blocks_invalid_timestamp_types(self) -> None:
        route = replace(_route(908, "sub_factor:98", 1008, 1, "90"), as_of_time=cast(Any, "2026-09-02"))
        issues: list[Any] = []
        _check_route_as_of_contract(datetime(2026, 9, 2, 1, 17), (route,), issues, "unit")
        assert any(issue.code == "RANK_ROUTE_AS_OF_TIME_MISSING" for issue in issues)

    def test_directed_payload_and_direction_conflict_cannot_be_silently_preferred(self) -> None:
        """同一 metric 的两个 directed 来源冲突时必须失败。"""

        snapshot = TestRouteScoreRecalculation._fixture()
        metric = snapshot.evaluation_metrics[0]
        metric = replace(
            metric,
            direction={
                **(metric.direction or {}),
                "directed_mean_ic": "999",
                "directed_mean_rank_ic": "999",
                "directed_icir": "999",
                "directed_rank_icir": "999",
            },
        )
        changed = replace(snapshot, evaluation_metrics=(metric,))
        service, _ = _service(changed)
        result = service.check_route_score_recalculation(changed)
        assert result.status == "FAIL"
        assert any(
            finding.code == "METRIC_DIRECTED_VALUE_CONFLICT"
            for finding in result.findings
        )
