"""Factor 4.0 calculation-audit orchestration and offline oracles."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Literal, Protocol, TypeAlias

import requests

from api.factor_data_mcp_api import (
    FactorDataMCPAPI,
    MCPJSONRPCError,
    MCPProtocolError,
    MCPResponse,
)
from db.factor4_calculation_repository import (
    CalculationAuditSnapshot,
    EnvironmentDailyRecord,
    EvaluationMetric,
    FactorDefinition,
    FactorDetail,
    FactorIdentity,
    FactorMembershipDifferences,
    FactorMembershipVersionIssue,
    FormulaEvidence,
    PublishedRoute,
    PublishedRouteSnapshot,
    RouteRankingEntry,
)


CalculationStatus: TypeAlias = Literal[
    "PASS",
    "FAIL",
    "BLOCKED_DATA_PRECONDITION",
    "BLOCKED_DOC",
]

_STATUS_PRIORITY: dict[CalculationStatus, int] = {
    "PASS": 0,
    "BLOCKED_DOC": 1,
    "BLOCKED_DATA_PRECONDITION": 2,
    "FAIL": 3,
}
_SCOPES = ("time_series", "cross_sectional")
_ISSUE_SAMPLE_LIMIT = 5
_DPO_REFS = ("sub_factor:161104", "sub_factor:161106", "sub_factor:161108")
_FIXED_HORIZON_EXPECTATIONS: dict[str, tuple[str, int, int]] = {
    "sub_factor:180": ("funding_diff2", 12, 24),
    "sub_factor:181": ("funding_diff2", 24, 48),
    "sub_factor:183": ("funding_diff2", 36, 72),
    "sub_factor:274": ("long_short_pct", 48, 48),
    "sub_factor:276": ("long_short_pct", 72, 72),
}
_IV_RV_REFS = ("sub_factor:161628", "sub_factor:161629", "sub_factor:161630")
# A source_detail_id is useful for tracing a formula, but it is not an
# immutable batch identity.  The other four fields are emitted by the
# evaluation writer and can identify the exact formula run/evidence row.
_STRONG_FORMULA_LINK_NAMES = (
    "formula_hash",
    "formula_version",
    "run_id",
    "formula_evidence_id",
)
_SOURCE_DETAIL_LINK_NAME = "source_detail_id"
_FORMULA_LINK_CONTAINER_NAMES = ("formula", "formula_identity", "formula_evidence")
_FORMULA_LINK_FIELD_NAMES = (
    *_STRONG_FORMULA_LINK_NAMES,
    "factor_version",
    _SOURCE_DETAIL_LINK_NAME,
    "evidence_id",
)
_FORMULA_LINK_ID_NAMES = {"formula_evidence_id", "evidence_id", _SOURCE_DETAIL_LINK_NAME}
_KNOWN_FUNCTION_NAMES = {
    "abs",
    "add",
    "astype",
    "atr",
    "clip",
    "correlation",
    "count",
    "corr",
    "delay",
    "div",
    "dpo",
    "diff",
    "exp",
    "fillna",
    "highest",
    "kurtosis",
    "log",
    "log1p",
    "lowest",
    "ma",
    "max",
    "mean",
    "median",
    "min",
    "minimum",
    "maximum",
    "pct_change",
    "percentile_rank",
    "quantile",
    "rank",
    "replace",
    "rsi",
    "round",
    "rolling",
    "rolling_sum",
    "rolling_vwap",
    "rolling_zscore",
    "shift",
    "sign",
    "skew",
    "skewness",
    "sqrt",
    "std",
    "sub",
    "sum",
    "tanh",
    "mul",
    "vwap",
    "where",
    "zscore",
}
_TERMINAL_MCP_DETAIL_ERROR = "MCP_TOOL_ERROR:EXPORT_BUDGET_EXCEEDED"
_NON_FIELD_NAMES = {
    "False",
    "None",
    "True",
    "df",
    "inf",
    "min_periods",
    "nan",
    "np",
    "pd",
    "periods",
    "period",
    "span",
    "halflife",
    "window",
}
_NON_FIELD_NAMES_CASEFOLD = frozenset(name.casefold() for name in _NON_FIELD_NAMES)
_KNOWN_FUNCTION_NAMES_CASEFOLD = frozenset(name.casefold() for name in _KNOWN_FUNCTION_NAMES)

# These operations either consume an unbounded history (cumulative/expanding
# windows) or depend on a grouping/context that this static oracle cannot
# recover.  Keeping them separate from ``_KNOWN_FUNCTION_NAMES`` ensures a
# formula is rejected with a useful reason instead of being treated as an
# unknown raw field or, worse, as a point-in-time expression.
_UNBOUNDED_TEMPORAL_NAMES = frozenset(
    {
        "cummax",
        "cummean",
        "cummin",
        "cumprod",
        "cumsum",
        "ewm",
        "expanding",
    }
)
_UNBOUNDED_TEMPORAL_NAMES_CASEFOLD = frozenset(
    name.casefold() for name in _UNBOUNDED_TEMPORAL_NAMES
)

# Functional temporal helpers have an explicit finite-window branch below.
# A qualified call such as ``np.mean(close, 24)`` does not: its positional
# arguments have different semantics in NumPy/Pandas and cannot safely be
# inferred from the catalog expression.  Reject those calls unless they are
# a supported method chain (for example ``close.rolling(24).mean()``).
_QUALIFIED_TEMPORAL_NAMES = frozenset(
    {
        "atr",
        "correlation",
        "corr",
        "delay",
        "diff",
        "dpo",
        "expanding",
        "ewm",
        "highest",
        "lowest",
        "ma",
        "max",
        "mean",
        "median",
        "min",
        "percentile_rank",
        "pct_change",
        "rolling",
        "rolling_sum",
        "rolling_vwap",
        "rolling_zscore",
        "rsi",
        "shift",
        "std",
        "sum",
        "vwap",
        "zscore",
    }
)
_QUALIFIED_TEMPORAL_NAMES_CASEFOLD = frozenset(
    name.casefold() for name in _QUALIFIED_TEMPORAL_NAMES
)
_QUALIFIED_NAMESPACE_ROOTS = frozenset({"np", "numpy", "pd", "pandas"})
_QUALIFIED_NAMESPACE_SAFE_NAMES = frozenset(
    {
        "abs",
        "add",
        "clip",
        "div",
        "exp",
        "log",
        "log1p",
        "maximum",
        "minimum",
        "mul",
        "replace",
        "round",
        "sign",
        "sqrt",
        "sub",
        "tanh",
        "where",
    }
)

# The offset oracle is deliberately finite and conservative.  A malformed or
# unbounded expression must be reported as an unresolved precondition instead
# of causing a large ``range`` allocation or being silently treated as a
# point-in-time expression.
_MAX_STATIC_PERIOD = 1_000_000
_TEMPORAL_KEYWORDS = frozenset(
    {
        "window",
        "period",
        "periods",
        "min_periods",
        "span",
        "halflife",
        "alpha",
    }
)
_STATIC_CONTROL_KEYWORDS = frozenset(
    {
        "adjust",
        "ascending",
        "axis",
        "center",
        "closed",
        "ddof",
        "engine",
        "engine_kwargs",
        "errors",
        "fill_value",
        "fill_method",
        "freq",
        "ignore_na",
        "inclusive",
        "inplace",
        "interpolation",
        "level",
        "limit",
        "method",
        "na_option",
        "numeric_only",
        "on",
        "pct",
        "skipna",
        "step",
        "to_replace",
        "value",
        "win_type",
        "dtype",
        "copy",
        "regex",
        "decimals",
        "q",
    }
)
_CONTROL_KEYWORDS = _TEMPORAL_KEYWORDS | _STATIC_CONTROL_KEYWORDS | frozenset(
    {"lower", "upper"}
)

# Keyword names are checked per operation.  This prevents a typo such as
# ``windoww=24`` from being mistaken for a data field.  The sets intentionally
# cover the pandas-style spellings emitted by the Factor 4.0 catalog while
# remaining closed to arbitrary user-defined parameters.
_FUNCTION_KEYWORDS: dict[str, frozenset[str]] = {
    "add": frozenset({"fill_value", "axis", "level"}),
    "abs": frozenset(),
    "astype": frozenset({"dtype", "copy", "errors"}),
    "atr": frozenset({"window", "period", "periods", "min_periods"}),
    "clip": frozenset({"lower", "upper", "axis"}),
    "correlation": frozenset({"window", "period", "periods", "min_periods"}),
    "count": frozenset({"axis", "numeric_only"}),
    "corr": frozenset({"window", "period", "periods", "min_periods"}),
    "delay": frozenset({"period", "periods", "window"}),
    "div": frozenset({"fill_value", "axis", "level"}),
    "dpo": frozenset({"window", "period", "periods", "min_periods"}),
    "diff": frozenset({"periods", "axis"}),
    "exp": frozenset(),
    "fillna": frozenset({"value", "method", "axis", "inplace", "limit", "downcast"}),
    "highest": frozenset({"window", "period", "periods", "min_periods"}),
    "kurtosis": frozenset({"axis", "skipna", "numeric_only"}),
    "log": frozenset(),
    "log1p": frozenset(),
    "lowest": frozenset({"window", "period", "periods", "min_periods"}),
    "ma": frozenset({"window", "period", "periods", "min_periods"}),
    "max": frozenset(
        {"window", "period", "periods", "min_periods", "axis", "skipna", "numeric_only"}
    ),
    "mean": frozenset(
        {"window", "period", "periods", "min_periods", "axis", "skipna", "numeric_only"}
    ),
    "median": frozenset({"axis", "skipna", "numeric_only"}),
    "min": frozenset(
        {"window", "period", "periods", "min_periods", "axis", "skipna", "numeric_only"}
    ),
    "maximum": frozenset(),
    "minimum": frozenset(),
    "mul": frozenset({"fill_value", "axis", "level"}),
    "pct_change": frozenset({"periods", "fill_method", "limit", "freq"}),
    "percentile_rank": frozenset({"window", "period", "periods", "min_periods", "method"}),
    "quantile": frozenset({"q", "interpolation", "numeric_only"}),
    "rank": frozenset({"axis", "method", "numeric_only", "na_option", "ascending", "pct"}),
    "replace": frozenset({"to_replace", "value", "inplace", "limit", "method", "regex"}),
    "rsi": frozenset({"window", "period", "periods", "min_periods"}),
    "rolling": frozenset(
        {
            "window",
            "period",
            "periods",
            "min_periods",
            "center",
            "win_type",
            "on",
            "axis",
            "closed",
            "step",
            "method",
        }
    ),
    "rolling_sum": frozenset({"window", "period", "periods", "min_periods"}),
    "rolling_vwap": frozenset({"window", "period", "periods", "min_periods"}),
    "rolling_zscore": frozenset({"window", "period", "periods", "min_periods"}),
    "round": frozenset({"decimals"}),
    "shift": frozenset({"periods", "axis", "fill_value"}),
    "sign": frozenset(),
    "skew": frozenset({"axis", "skipna", "numeric_only"}),
    "skewness": frozenset({"axis", "skipna", "numeric_only"}),
    "sqrt": frozenset(),
    "std": frozenset(
        {
            "window",
            "period",
            "periods",
            "min_periods",
            "axis",
            "skipna",
            "ddof",
            "numeric_only",
        }
    ),
    "sum": frozenset(
        {
            "window",
            "period",
            "periods",
            "min_periods",
            "axis",
            "skipna",
            "numeric_only",
        }
    ),
    "sub": frozenset({"fill_value", "axis", "level"}),
    "tanh": frozenset(),
    "vwap": frozenset({"window", "period", "periods", "min_periods"}),
    "where": frozenset({"cond", "condition", "x", "y", "other", "inplace", "axis", "level"}),
    "zscore": frozenset({"window", "period", "periods", "min_periods"}),
}


class FormulaOffsetError(ValueError):
    """Indicate that a temporal formula cannot be resolved statically."""


class CalculationSnapshotRepository(Protocol):
    """Describe the read-only repository entry point used by this service."""

    def read_calculation_snapshot(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> CalculationAuditSnapshot:
        """Return one coherent published calculation snapshot.

        Implementations may raise their repository-specific read exception. No
        database write is permitted.
        """

    def read_published_route_snapshot(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> PublishedRouteSnapshot:
        """Return a lightweight repeat-read snapshot of active route ranks.

        Implementations may raise their repository-specific read exception. No
        database write is permitted.
        """


@dataclass(frozen=True)
class CalculationIssue:
    """Describe one failed or blocked calculation assertion."""

    status: CalculationStatus
    code: str
    message: str
    factor_ref: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalculationCheckResult:
    """Hold the deterministic result of one Factor 4.0 calculation sub-case."""

    case_id: str
    title: str
    status: CalculationStatus
    summary: str
    checked_count: int
    findings: tuple[CalculationIssue, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def issues(self) -> tuple[CalculationIssue, ...]:
        """Return ``findings`` for compatibility with early Service callers."""

        return self.findings


@dataclass(frozen=True)
class Factor4CalculationReport:
    """Aggregate explicitly selected calculation checks for one published batch."""

    batch_uid: str
    captured_at: datetime
    market_scope: str
    route_profile_key: str
    status: CalculationStatus
    checks: tuple[CalculationCheckResult, ...]

    @property
    def results(self) -> tuple[CalculationCheckResult, ...]:
        """Return ``checks`` for compatibility with early Service callers."""

        return self.checks


def formula_dependency_offsets(
    expression: str,
    *,
    variables: Mapping[str, int] | None = None,
) -> tuple[int, ...]:
    """Return all raw-series offsets required by a Python-style expression.

    ``diff``, ``pct_change``, ``shift`` and ``rolling`` are evaluated through
    chained calls. Functional ``mean(close, n)`` and method-style
    ``close.rolling(n).mean()`` are both supported. Temporal arguments may use
    static integer arithmetic. ``FormulaOffsetError`` is raised for malformed
    expressions, unsupported calls/AST nodes, negative values, or dynamic
    temporal parameters; values are never guessed.  ``variables`` may provide
    an explicit integer binding for a formula parameter such as ``window``;
    names not present in that map remain dynamic when used as a temporal
    argument.
    """

    if not isinstance(expression, str) or not expression.strip():
        raise FormulaOffsetError("formula expression must be a non-empty string")
    node = _formula_expression_node(expression)
    if node is None:
        raise FormulaOffsetError("invalid formula syntax or executable wrapper")
    return tuple(sorted(_dependency_offsets(node, variables)))


def dpo_reference_values(window: int = 60, index: int = 150) -> tuple[Decimal, Decimal]:
    """Return the correct and historical-wrong DPO values for the fixed series.

    The synthetic close series is ``100 + i*i/17 + 0.13*(i % 7)``. ``window``
    and ``index`` must make both rolling windows available. The first returned
    value is ``SMA(close, window) - close.shift(window // 2 + 1)``; the second
    is the historical ``-(close - SMA(close, window).shift(...))`` value.
    ``ValueError`` is raised when the requested point has insufficient history.
    """

    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("window must be a positive integer")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("index must be a non-negative integer")
    shift = window // 2 + 1
    if index < window + shift - 1:
        raise ValueError("index does not provide enough history for both DPO formulas")

    def close(position: int) -> Decimal:
        return Decimal(100) + Decimal(position * position) / Decimal(17) + (
            Decimal(13) * Decimal(position % 7) / Decimal(100)
        )

    def sma(end: int) -> Decimal:
        return sum(
            (close(position) for position in range(end - window + 1, end + 1)),
            Decimal(0),
        ) / Decimal(window)

    correct = sma(index) - close(index - shift)
    historical_wrong = sma(index - shift) - close(index)
    return correct, historical_wrong


class Factor4CalculationService:
    """Orchestrate Factor 4.0 MCP/DB calculation evidence checks."""

    def __init__(
        self,
        repository: CalculationSnapshotRepository,
        mcp_api: FactorDataMCPAPI,
        protocol_version: str = "2025-06-18",
    ) -> None:
        """Initialize the read-only calculation service.

        ``repository`` supplies coherent database snapshots, ``mcp_api`` reads
        public Factor Data projections, and ``protocol_version`` is requested
        during MCP initialization. Blank protocol versions raise ``ValueError``.
        The constructor performs no I/O.
        """

        normalized_protocol = str(protocol_version).strip()
        if not normalized_protocol:
            raise ValueError("protocol_version must not be blank")
        self._repository = repository
        self._mcp_api = mcp_api
        self._protocol_version = normalized_protocol
        self._detail_cache: dict[str, dict[str, Any]] = {}
        self._detail_errors: dict[str, str] = {}
        self._formula_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._formula_errors: dict[tuple[Any, ...], str] = {}
        self._mcp_cache_scope: tuple[Any, ...] | None = None

    def run_r0_checks(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> Factor4CalculationReport:
        """Execute the five currently decidable Factor 4.0 calculation checks.

        The method initializes MCP, captures one DB snapshot, runs formula,
        validity and score checks, captures a second snapshot, and evaluates
        rank stability. It returns a structured report and performs no pytest
        assertion or database write. Repository, network, HTTP and MCP protocol
        exceptions are propagated because they prevent creation of a trustworthy
        batch-level report. Frozen-member/metric drift is retained as a
        structured failure in the report even if an individual check adapter is
        replaced.
        """

        self._reset_mcp_cache()
        self._mcp_api.initialize(protocol_version=self._protocol_version)
        self._mcp_api.notify_initialized()
        snapshot = self._repository.read_calculation_snapshot(
            market_scope=market_scope,
            route_profile_key=route_profile_key,
        )
        static = self.check_formula_static_consistency(snapshot)
        regressions = self.check_known_formula_regressions(snapshot)
        validity = self.check_any_valid_scope(snapshot)
        # Keep the batch-level membership guard effective even when an
        # individual check is replaced by an adapter or test double.
        validity = _with_additional_findings(
            validity,
            _membership_difference_issues(snapshot),
        )
        scoring = self.check_route_score_recalculation(snapshot)
        repeated = self._repository.read_published_route_snapshot(
            market_scope=market_scope,
            route_profile_key=route_profile_key,
        )
        ranking = self.check_rank_stability(snapshot, repeated)
        results = (validity, scoring, ranking, static, regressions)
        return Factor4CalculationReport(
            batch_uid=snapshot.batch.batch_uid,
            captured_at=snapshot.captured_at,
            market_scope=snapshot.batch.market_scope,
            route_profile_key=snapshot.batch.route_profile_key,
            status=_highest_status(result.status for result in results),
            checks=results,
        )

    def check_formula_static_consistency(
        self,
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Check DB definitions, MCP projections and immutable formula evidence.

        ``snapshot`` must contain the batch's frozen factor identities. Every
        factor is checked using batched executable-detail reads (at most 50 refs
        per call). An exact ``factor_get_formula`` read is made only for an
        immutable evidence row selected by a metric's strong formula link;
        unbound completed rows are reported as historical data that cannot
        prove batch execution. Explicit projection or identity contradictions
        are ``FAIL``. Missing detail/schema/evidence, or a metric that has no
        provable formula hash/version/run link, is ``BLOCKED_DATA_PRECONDITION``.
        The latest completed formula is never inferred to be the formula used
        by a batch without such a link. An explicit frozen-member/metric
        difference is also a ``FAIL`` finding; an unavailable membership
        reconciliation is data-blocked.
        """

        return self._check_formula_consistency(snapshot, include_internal_semantics=True)

    def run_result_checks(
        self,
        market_scope: str = "all",
        route_profile_key: str = "default",
    ) -> Factor4CalculationReport:
        """Audit persisted admission, scores, ranks and MCP formula projections only.

        The explicit partition is read through the repository and MCP is initialized
        once. Returns four structured checks without scanning operator mathematics,
        DPO/horizon regressions or normalized-formula equivalence. Network, protocol
        and database exceptions propagate; no computation or database write occurs.
        """
        self._reset_mcp_cache()
        self._mcp_api.initialize(protocol_version=self._protocol_version)
        self._mcp_api.notify_initialized()
        snapshot = self._repository.read_calculation_snapshot(
            market_scope=market_scope, route_profile_key=route_profile_key,
        )
        formula = self.check_formula_result_consistency(snapshot)
        validity = _with_additional_findings(
            self.check_any_valid_scope(snapshot), _membership_difference_issues(snapshot),
        )
        scoring = self.check_route_score_recalculation(snapshot)
        repeated = self._repository.read_published_route_snapshot(
            market_scope=market_scope, route_profile_key=route_profile_key,
        )
        ranking = self.check_rank_stability(snapshot, repeated)
        checks = (validity, scoring, ranking, formula)
        return Factor4CalculationReport(
            batch_uid=snapshot.batch.batch_uid, captured_at=snapshot.captured_at,
            market_scope=snapshot.batch.market_scope,
            route_profile_key=snapshot.batch.route_profile_key,
            status=_highest_status(check.status for check in checks), checks=checks,
        )

    def check_formula_result_consistency(
        self, snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Reconcile persisted formula identities and their exact MCP projections.

        Input is one coherent published snapshot. Returns identity, version, source,
        field and expression-projection findings, without interpreting the operator
        mathematics. Missing strong links remain data preconditions; transport and
        protocol failures propagate exactly as in the historical formula audit.
        """
        return self._check_formula_consistency(snapshot, include_internal_semantics=False)

    def _check_formula_consistency(
        self, snapshot: CalculationAuditSnapshot, *, include_internal_semantics: bool,
    ) -> CalculationCheckResult:

        self._ensure_mcp_cache_scope(snapshot)
        issues: list[CalculationIssue] = _membership_difference_issues(snapshot)
        refs = sorted(
            {definition.factor_ref for definition in snapshot.definitions}
            | {metric.factor_ref for metric in snapshot.evaluation_metrics}
        )
        if not refs:
            issues.append(_blocked("FORMULA_FACTOR_SET_MISSING", "批次中没有可核验的因子身份。"))
            return _result("CALC-510-A", "公式静态一致性", 0, issues)

        self._load_mcp_details(refs)
        definitions = _group_by_ref(snapshot.definitions)
        details = _group_by_ref(snapshot.details)
        evidence_by_ref = _group_by_ref(snapshot.formula_evidence)
        metrics_by_ref = _group_by_ref(snapshot.evaluation_metrics)
        metric_link_issues: list[CalculationIssue] = []

        for factor_ref in refs:
            factor_definitions = definitions.get(factor_ref, [])
            factor_details = details.get(factor_ref, [])
            factor_evidence = evidence_by_ref.get(factor_ref, [])
            factor_metrics = metrics_by_ref.get(factor_ref, [])
            mcp_detail = self._detail_cache.get(factor_ref)
            if len(factor_definitions) != 1:
                issues.append(
                    _blocked(
                        "FORMULA_DEFINITION_NOT_UNIQUE",
                        "批次因子缺少唯一当前定义。",
                        factor_ref,
                        count=len(factor_definitions),
                    )
                )
            if not factor_details:
                issues.append(_blocked("FORMULA_DETAIL_MISSING", "数据库缺少公式详情。", factor_ref))
            if mcp_detail is None:
                issues.append(
                    _blocked(
                        "MCP_EXECUTABLE_DETAIL_MISSING",
                        "MCP 未返回可执行因子详情。",
                        factor_ref,
                        reason=self._detail_errors.get(factor_ref),
                    )
                )
            elif mcp_detail.get("factor_ref") != factor_ref:
                issues.append(
                    _failed(
                        "MCP_DETAIL_FACTOR_IDENTITY_MISMATCH",
                        "MCP 详情返回了不同的 factor_ref。",
                        factor_ref,
                        returned_factor_ref=mcp_detail.get("factor_ref"),
                    )
                )

            current_detail = _latest_detail(factor_details)
            definition = factor_definitions[0] if len(factor_definitions) == 1 else None
            if definition is not None and mcp_detail is not None:
                _compare_definition_projection(
                    definition, current_detail, mcp_detail, issues,
                    include_internal_semantics=include_internal_semantics,
                )

            if not factor_evidence:
                issues.append(
                    _blocked(
                        "IMMUTABLE_FORMULA_EVIDENCE_MISSING",
                        "没有 completed run 的不可变公式证据。",
                        factor_ref,
                    )
                )
            # Formula evidence is not batch evidence merely because it is the
            # newest completed row in the catalog.  Resolve it through the
            # metric's immutable formula identity first.  A snapshot without
            # any metrics is the offline/unit-fixture mode used by this
            # service's deterministic formula checks; in that mode the
            # supplied evidence is intentionally treated as the explicit
            # input rather than inferred to belong to a real publication.
            bound_ids: set[int] = set()
            if factor_metrics:
                for metric in factor_metrics:
                    linked = _check_metric_formula_link(
                        metric,
                        factor_evidence,
                        metric_link_issues,
                    )
                    if linked is not None:
                        bound_ids.add(linked.id)
                batch_evidence = tuple(
                    formula for formula in factor_evidence if formula.id in bound_ids
                )
                historical_evidence = tuple(
                    formula for formula in factor_evidence if formula.id not in bound_ids
                )
            else:
                batch_evidence = tuple(factor_evidence)
                historical_evidence = ()

            if not factor_metrics:
                incomplete = tuple(
                    formula
                    for formula in batch_evidence
                    if not _is_completed_formula_evidence(formula)
                )
                if incomplete:
                    issues.append(
                        _blocked(
                            "FORMULA_EVIDENCE_NOT_COMPLETED",
                            "公式 evidence 不是 completed run，不能作为执行证据。",
                            factor_ref,
                            evidence_id_samples=[
                                formula.id for formula in incomplete[:_ISSUE_SAMPLE_LIMIT]
                            ],
                        )
                    )
                    batch_evidence = tuple(
                        formula for formula in batch_evidence if _is_completed_formula_evidence(formula)
                    )

            if historical_evidence:
                issues.append(
                    _blocked(
                        "HISTORICAL_FORMULA_EVIDENCE_UNBOUND",
                        "存在 completed evidence，但 batch metric 没有强公式链接，不能证明其是否用于当前发布批次；历史表达式不参与当前 FAIL。",
                        factor_ref,
                        evidence_count=len(historical_evidence),
                        evidence_id_samples=[
                            formula.id for formula in historical_evidence[:_ISSUE_SAMPLE_LIMIT]
                        ],
                        run_id_samples=[
                            formula.run_id for formula in historical_evidence[:_ISSUE_SAMPLE_LIMIT]
                        ],
                    )
                )

            for formula in batch_evidence:
                self._load_mcp_formula(formula)
                formula_key = _formula_key(formula)
                mcp_formula = self._formula_cache.get(formula_key)
                if formula.source_detail_id is None or not any(
                    detail.id == formula.source_detail_id for detail in factor_details
                ):
                    issues.append(
                        _blocked(
                            "FORMULA_SOURCE_DETAIL_UNRESOLVED",
                            "不可变公式证据无法回指同一因子的 detail 行。",
                            factor_ref,
                            source_detail_id=formula.source_detail_id,
                        )
                    )
                if mcp_formula is None:
                    issues.append(
                        _blocked(
                            "MCP_FORMULA_EVIDENCE_MISSING",
                            "MCP 未返回精确 Run 的公式证据。",
                            factor_ref,
                            run_id=formula.run_id,
                            reason=self._formula_errors.get(formula_key),
                        )
                    )
                    continue
                _compare_formula_projection(
                    formula, mcp_formula, issues,
                    include_internal_semantics=include_internal_semantics,
                )
                schema_fields = _definition_schema_fields(current_detail, mcp_detail)
                required = {str(value) for value in formula.required_fields if str(value)}
                if not required or not schema_fields:
                    issues.append(
                        _blocked(
                            "FORMULA_REQUIRED_FIELD_SCHEMA_MISSING",
                            "公式 required_fields 或详情数据 schema 不完整。",
                            factor_ref,
                        )
                    )
                elif not required.issubset(schema_fields):
                    issues.append(
                        _failed(
                            "FORMULA_REQUIRED_FIELD_SCHEMA_MISMATCH",
                            "公式 required_fields 未被详情数据 schema 完整覆盖。",
                            factor_ref,
                            missing_fields=sorted(required - schema_fields),
                        )
                    )

                _compare_current_formula_identity(
                    formula,
                    definition,
                    current_detail,
                    mcp_detail,
                    issues,
                )

        issues.extend(_aggregate_metric_formula_issues(metric_link_issues))

        return _result(
            "CALC-510-A",
            "公式静态一致性" if include_internal_semantics else "公式结果身份与 MCP 输出一致性",
            len(refs),
            issues,
            factor_count=len(refs),
            formula_evidence_count=len(snapshot.formula_evidence),
        )

    def check_known_formula_regressions(
        self,
        snapshot: CalculationAuditSnapshot,
        *,
        family: Literal["all", "dpo", "fixed_horizon", "iv_rv"] = "all",
    ) -> CalculationCheckResult:
        """Recheck DPO, fixed-horizon and IV/RV formula defect families.

        Current DB definitions, the latest DB detail, and the MCP executable
        detail form the current layer. Immutable formula rows enter the batch
        layer only when a batch metric strongly links them; unbound completed
        rows are reported as historical and never drive a current ``FAIL``.
        Any current or batch-bound expression that retains a known wrong
        semantic is ``FAIL`` with the Bug Registry's fixed Chinese title.
        Missing current/batch evidence is ``BLOCKED_DATA_PRECONDITION``. VWAP
        is intentionally outside this method. ``family`` optionally isolates a
        single documented regression family so unrelated missing factors cannot
        block its business Case. Invalid family names raise ``ValueError``.
        """

        family_targets = {
            "all": (*_DPO_REFS, *_FIXED_HORIZON_EXPECTATIONS, *_IV_RV_REFS),
            "dpo": _DPO_REFS,
            "fixed_horizon": tuple(_FIXED_HORIZON_EXPECTATIONS),
            "iv_rv": _IV_RV_REFS,
        }
        if family not in family_targets:
            raise ValueError("unsupported known formula regression family")
        self._ensure_mcp_cache_scope(snapshot)
        targets = family_targets[family]
        self._load_mcp_details(targets)
        definitions = _group_by_ref(snapshot.definitions)
        details = _group_by_ref(snapshot.details)
        evidence_by_ref = _group_by_ref(snapshot.formula_evidence)
        metrics_by_ref = _group_by_ref(snapshot.evaluation_metrics)
        issues: list[CalculationIssue] = []
        metric_link_issues: list[CalculationIssue] = []

        for factor_ref in targets:
            factor_evidence = evidence_by_ref.get(factor_ref, [])
            factor_metrics = metrics_by_ref.get(factor_ref, [])
            if factor_metrics:
                bound_ids: set[int] = set()
                for metric in factor_metrics:
                    linked = _check_metric_formula_link(
                        metric,
                        factor_evidence,
                        metric_link_issues,
                    )
                    if linked is not None:
                        bound_ids.add(linked.id)
                batch_evidence = tuple(
                    evidence for evidence in factor_evidence if evidence.id in bound_ids
                )
                historical_evidence = tuple(
                    evidence for evidence in factor_evidence if evidence.id not in bound_ids
                )
            else:
                # No metric rows means this is an offline direct-regression
                # fixture (or a catalog-only target outside the active batch).
                # In that deliberately explicit mode, supplied evidence is
                # checked as an input rather than silently called historical.
                batch_evidence = tuple(factor_evidence)
                historical_evidence = ()

            if not factor_metrics:
                incomplete = tuple(
                    evidence
                    for evidence in batch_evidence
                    if not _is_completed_formula_evidence(evidence)
                )
                if incomplete:
                    issues.append(
                        _blocked(
                            "FORMULA_EVIDENCE_NOT_COMPLETED",
                            "公式 evidence 不是 completed run，不能作为执行证据。",
                            factor_ref,
                            evidence_id_samples=[
                                evidence.id for evidence in incomplete[:_ISSUE_SAMPLE_LIMIT]
                            ],
                        )
                    )
                    batch_evidence = tuple(
                        evidence
                        for evidence in batch_evidence
                        if _is_completed_formula_evidence(evidence)
                    )

            for evidence in batch_evidence:
                self._load_mcp_formula(evidence)
                projection = self._formula_cache.get(_formula_key(evidence))
                if projection is not None:
                    _compare_formula_projection(evidence, projection, issues)

            if historical_evidence:
                issues.append(
                    _blocked(
                        "HISTORICAL_FORMULA_EVIDENCE_UNBOUND",
                        "存在 completed evidence，但 batch metric 没有强公式链接，不能证明其是否用于当前发布批次；历史表达式不参与当前 FAIL。",
                        factor_ref,
                        evidence_count=len(historical_evidence),
                        evidence_id_samples=[
                            evidence.id for evidence in historical_evidence[:_ISSUE_SAMPLE_LIMIT]
                        ],
                        run_id_samples=[
                            evidence.run_id for evidence in historical_evidence[:_ISSUE_SAMPLE_LIMIT]
                        ],
                    )
                )

            current_detail = _latest_detail(details.get(factor_ref, []))
            current_definition = _latest_definition(definitions.get(factor_ref, []))
            current_expressions = _collect_formula_expressions(
                factor_ref,
                (current_definition,) if current_definition is not None else (),
                (current_detail,) if current_detail is not None else (),
                (),
                self._detail_cache.get(factor_ref),
                self._formula_cache,
            )
            batch_expressions = _collect_formula_expressions(
                factor_ref,
                (),
                (),
                batch_evidence,
                None,
                self._formula_cache,
            )
            expressions = [*current_expressions, *batch_expressions]

            has_current_source = bool(current_expressions)
            if not has_current_source:
                issues.append(
                    _blocked(
                        "KNOWN_FORMULA_CURRENT_SOURCE_MISSING",
                        "已知回归因子缺少当前 DB 定义、最新 detail 或 MCP 可执行详情。",
                        factor_ref,
                    )
                )
            if factor_metrics and not factor_evidence:
                issues.append(
                    _blocked(
                        "KNOWN_FORMULA_EVIDENCE_MISSING",
                        "当前发布批次的已知回归因子没有 completed-run 公式证据。",
                        factor_ref,
                    )
                )
            if self._detail_cache.get(factor_ref) is None:
                issues.append(
                    _blocked(
                        "KNOWN_FORMULA_MCP_PROJECTION_MISSING",
                        "已知回归因子的 MCP 当前可执行详情不可读取。",
                        factor_ref,
                    )
                )
            if any(_formula_key(row) not in self._formula_cache for row in batch_evidence):
                issues.append(
                    _blocked(
                        "KNOWN_FORMULA_MCP_FORMULA_EVIDENCE_MISSING",
                        "已知回归因子的 batch-bound MCP 精确公式证据不可读取。",
                        factor_ref,
                    )
                )
            if not expressions:
                continue

            if factor_ref in _DPO_REFS:
                window = _declared_window(
                    (current_definition,) if current_definition is not None else (),
                    (current_detail,) if current_detail is not None else (),
                    self._detail_cache.get(factor_ref),
                )
                if window is None:
                    issues.append(
                        _blocked(
                            "DPO_DECLARED_WINDOW_UNRESOLVED",
                            "DPO 当前冻结/详情声明无法唯一解析窗口，不能套用固定期数。",
                            factor_ref,
                        )
                    )
                    continue
                wrong_sources = [
                    source for source, expression in expressions
                    if not _is_correct_dpo(expression, window=window)
                ]
                if wrong_sources:
                    oracle_evidence: dict[str, Any] = {"declared_window": window}
                    if window == 60:
                        correct, historical = dpo_reference_values(window=window)
                        oracle_evidence.update(
                            {
                                "oracle_index": 150,
                                "correct_value": str(correct),
                                "historical_wrong_value": str(historical),
                            }
                        )
                    issues.append(
                        _failed(
                            "F4-DPO-FORMULA",
                            "DPO 公式错误地位移均线而非价格序列",
                            factor_ref,
                            wrong_sources=wrong_sources,
                            **oracle_evidence,
                        )
                    )
            elif factor_ref in _FIXED_HORIZON_EXPECTATIONS:
                oracle_family, period, contracted_window = _FIXED_HORIZON_EXPECTATIONS[factor_ref]
                declared_window = _declared_window(
                    (current_definition,) if current_definition is not None else (),
                    (current_detail,) if current_detail is not None else (),
                    self._detail_cache.get(factor_ref),
                )
                if declared_window is None:
                    issues.append(
                        _blocked(
                            "FIXED_HORIZON_DECLARED_WINDOW_UNRESOLVED",
                            "固定周期因子当前声明窗口不可唯一解析，不能应用族 Oracle。",
                            factor_ref,
                        )
                    )
                    continue
                if declared_window != contracted_window:
                    issues.append(
                        _failed(
                            "F4-FIXED-HORIZON-FORMULA",
                            "固定周期因子公式未应用声明窗口",
                            factor_ref,
                            registry_window=contracted_window,
                            current_declared_window=declared_window,
                        )
                    )
                    continue
                wrong_sources = [
                    source for source, expression in expressions
                    if not _is_correct_fixed_horizon(
                        expression,
                        oracle_family,
                        period,
                        declared_window,
                    )
                ]
                if wrong_sources:
                    issues.append(
                        _failed(
                            "F4-FIXED-HORIZON-FORMULA",
                            "固定周期因子公式未应用声明窗口",
                            factor_ref,
                            expected_family=oracle_family,
                            expected_period=period,
                            wrong_sources=wrong_sources,
                        )
                    )
            else:
                wrong_sources = [
                    source for source, expression in expressions
                    if not _is_iv_rv_difference(expression)
                ]
                required_field_sources = _collect_required_field_sets(
                    factor_ref,
                    (current_detail,) if current_detail is not None else (),
                    batch_evidence,
                    self._detail_cache.get(factor_ref),
                    self._formula_cache,
                )
                expected = {"atm_iv", "realized_vol"}
                wrong_field_sources = [
                    source for source, values in required_field_sources
                    if not expected.issubset({value.casefold() for value in values})
                ]
                if wrong_sources or wrong_field_sources:
                    issues.append(
                        _failed(
                            "F4-IV-RV-DEFINITION-RUNTIME-MISMATCH",
                            "IV/RV 因子定义与实际执行公式及输入字段不一致",
                            factor_ref,
                            wrong_formula_sources=wrong_sources,
                            wrong_required_field_sources=wrong_field_sources,
                        )
                    )

        issues.extend(_aggregate_metric_formula_issues(metric_link_issues))
        return _result(
            "CALC-510-C",
            "已知因子公式回归",
            len(targets),
            issues,
            excluded=("VWAP",),
            family=family,
        )

    def check_formula_integrity(
        self,
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Audit immutable formula evidence for executable integrity invariants.

        This check replaces the former standalone ``formula_integrity_audit``
        script.  It validates every evidence row's completed-run linkage,
        required metadata and Python-like expression syntax, and rejects
        rolling/VWAP calls that omit a window argument.  It only consumes the
        coherent DB snapshot and performs no network or write operation.
        """

        issues: list[CalculationIssue] = []
        checked = len(snapshot.formula_evidence)
        if not snapshot.formula_evidence:
            return _result(
                "CALC-510-FORMULA-INTEGRITY",
                "公式证据完整性",
                0,
                [_blocked("FORMULA_EVIDENCE_EMPTY", "批次没有不可变公式证据。")],
            )
        for evidence in snapshot.formula_evidence:
            ref = evidence.factor_ref
            if evidence.run_status != "completed":
                issues.append(_blocked(
                    "FORMULA_RUN_NOT_COMPLETED",
                    "公式证据关联的 Run 不是 completed。",
                    ref, evidence_id=evidence.id, run_id=evidence.run_id,
                ))
            if evidence.run_completed_at < evidence.recorded_at:
                issues.append(_failed(
                    "FORMULA_COMPLETION_BEFORE_RECORDING",
                    "公式证据 recorded_at 晚于其 Run 完成时间。",
                    ref, evidence_id=evidence.id, run_id=evidence.run_id,
                ))
            if not evidence.expression or not str(evidence.expression).strip():
                issues.append(_failed("FORMULA_EXPRESSION_EMPTY", "公式表达式为空。", ref, evidence_id=evidence.id))
                continue
            try:
                tree = ast.parse(str(evidence.expression), mode="eval")
            except SyntaxError as exc:
                issues.append(_failed(
                    "FORMULA_EXPRESSION_PARSE_ERROR", "公式表达式无法解析。", ref,
                    evidence_id=evidence.id, reason=exc.msg,
                ))
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                ).casefold()
                if name in {"rolling", "vwap", "rolling_vwap"}:
                    has_window = any(
                        keyword.arg is not None
                        and keyword.arg.casefold() in {"window", "period", "periods"}
                        for keyword in node.keywords
                    )
                    # A single positional argument is a valid catalog helper
                    # form (for example ``vwap(24)`` or ``rolling(24)``).
                    # Only a call with no positional/period argument is
                    # unambiguously cumulative and therefore defective here.
                    missing_window = not has_window and not node.args
                    if (
                        name in {"vwap", "rolling_vwap"}
                        and not has_window
                        and len(node.args) == 2
                        and not isinstance(node.args[1], ast.Constant)
                    ):
                        # Explicit price/volume form requires a statically
                        # resolvable final window; a series name here is the
                        # legacy cumulative-VWAP shape.
                        missing_window = True
                    if missing_window:
                        issues.append(_failed(
                            "FORMULA_WINDOW_MISSING",
                            "rolling/VWAP 公式未声明窗口，无法证明滚动窗口语义。",
                            ref, evidence_id=evidence.id, function=name,
                        ))
            if not evidence.formula_hash or not evidence.formula_version:
                issues.append(_blocked(
                    "FORMULA_IDENTITY_METADATA_MISSING",
                    "公式证据缺少不可变 hash/version。",
                    ref, evidence_id=evidence.id,
                ))
            if not evidence.required_fields:
                issues.append(_blocked(
                    "FORMULA_REQUIRED_FIELDS_MISSING",
                    "公式证据缺少 required_fields。",
                    ref, evidence_id=evidence.id,
                ))
            if not evidence.metadata_complete:
                issues.append(_blocked(
                    "FORMULA_METADATA_INCOMPLETE",
                    "公式证据 metadata_complete 为 false。",
                    ref, evidence_id=evidence.id,
                ))
        return _result(
            "CALC-510-FORMULA-INTEGRITY",
            "公式证据完整性",
            checked,
            issues,
            formula_evidence_count=checked,
        )


    def check_any_valid_scope(
        self,
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Verify the TS/CS any-valid-scope truth table for paired metrics.

        Metrics are paired only inside the same batch, factor/version, label and
        sample identity. The Repository-provided scope-neutral identity hash is
        preferred; canonical full ``metric_identity`` is the fallback. A scope
        is valid only when ``metric_status=success`` and ``is_valid is True``.
        All four TS-only/CS-only/both/neither branches must be present. Missing
        both identity forms or ambiguous evidence blocks the check; an explicit
        route-eligibility contradiction fails it. Frozen-member/metric drift is
        an additional structured ``FAIL`` finding.
        """

        pairs, pair_issues = _metric_pairs(snapshot.evaluation_metrics)
        issues = [*_membership_difference_issues(snapshot), *pair_issues]
        seen_classes: set[str] = set()
        checked = 0
        for pair_key, pair in pairs.items():
            if set(pair) != set(_SCOPES):
                issues.append(
                    _blocked(
                        "VALIDITY_SCOPE_PAIR_INCOMPLETE",
                        "同一完整 metric identity 未同时取得 TS 和 CS 行。",
                        pair.get("time_series", pair.get("cross_sectional")).factor_ref,
                        pair_key=pair_key,
                        present_scopes=sorted(pair),
                    )
                )
                continue
            checked += 1
            valid_scopes = [scope for scope in _SCOPES if _metric_is_valid(pair[scope])]
            invalid_scopes = [scope for scope in _SCOPES if scope not in valid_scopes]
            truth_class = (
                "both" if len(valid_scopes) == 2
                else "ts_only" if valid_scopes == ["time_series"]
                else "cs_only" if valid_scopes == ["cross_sectional"]
                else "neither"
            )
            seen_classes.add(truth_class)
            for metric in pair.values():
                eligibility = metric.route_eligibility
                if not isinstance(eligibility, Mapping):
                    issues.append(
                        _blocked(
                            "ROUTE_ELIGIBILITY_EVIDENCE_MISSING",
                            "metric 缺少 route_eligibility 证据。",
                            metric.factor_ref,
                            metric_id=metric.id,
                        )
                    )
                    continue
                _check_any_valid_eligibility(
                    metric,
                    eligibility,
                    valid_scopes,
                    invalid_scopes,
                    issues,
                )

        missing_classes = sorted({"ts_only", "cs_only", "both", "neither"} - seen_classes)
        if missing_classes:
            issues.append(
                _blocked(
                    "VALIDITY_TRUTH_TABLE_INCOMPLETE",
                    "当前快照不能覆盖任一维有效真值表的四种组合。",
                    missing_classes=missing_classes,
                    seen_classes=sorted(seen_classes),
                )
            )
        return _result(
            "CALC-501-C",
            "TS/CS 任一维度有效真值表",
            checked,
            issues,
            truth_classes=sorted(seen_classes),
            contract_ref="current_task_acceptance:any_valid_scope",
        )

    def check_route_score_recalculation(
        self,
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Recalculate published route scores, confidence and signed metrics.

        Configured weights are normalized over valid scopes only. Decimal
        arithmetic and ``ROUND_HALF_UP`` are used, and each result is quantized
        to the persisted Decimal's own scale before exact comparison. Missing
        weights, metric IDs, score components, direction or directed values
        blocks the affected route; explicit identity or numeric disagreement
        fails it. No fixed epsilon is invented. A frozen-member/metric
        reconciliation failure is included before score evidence is judged.
        """

        issues: list[CalculationIssue] = _membership_difference_issues(snapshot)
        metrics_by_id = {metric.id: metric for metric in snapshot.evaluation_metrics}
        if len(metrics_by_id) != len(snapshot.evaluation_metrics):
            issues.append(_blocked("METRIC_ID_NOT_UNIQUE", "同一快照中的 metric ID 不唯一。"))
        metric_pairs, pair_issues = _metric_pairs(snapshot.evaluation_metrics)
        issues.extend(pair_issues)
        if not snapshot.routes:
            issues.append(_blocked("ROUTE_SAMPLE_MISSING", "当前发布没有可重算的 route 样本。"))

        configured_weights, configured_weight_path, configured_weight_error = (
            _configured_route_profile_weights(snapshot.batch)
        )
        if configured_weights is None:
            weight_issue_code = (
                "ROUTE_BATCH_PROFILE_WEIGHTS_CONFLICT"
                if configured_weight_error
                and configured_weight_error.startswith("conflicting profile weight mappings")
                else "ROUTE_BATCH_PROFILE_WEIGHTS_MISSING"
            )
            weight_issue_message = (
                "batch evaluation_config 的多个 profile 权重来源不一致；不能静默选择其中一份。"
                if weight_issue_code == "ROUTE_BATCH_PROFILE_WEIGHTS_CONFLICT"
                else "batch evaluation_config 未提供可解析的 TS/CS profile 权重；不能回退 route evidence 或客户端默认值。"
            )
            issues.append(
                _blocked(
                    weight_issue_code,
                    weight_issue_message,
                    path=configured_weight_path,
                    reason=configured_weight_error,
                    route_profile_key=snapshot.batch.route_profile_key,
                )
            )
        batch_config = snapshot.batch.evaluation_config
        minimum_raw = (
            batch_config.get("minimum_route_score")
            if isinstance(batch_config, Mapping)
            else None
        )
        minimum_route_score = _to_decimal(minimum_raw)
        if minimum_route_score is None:
            issues.append(
                _blocked(
                    "ROUTE_BATCH_MINIMUM_SCORE_MISSING",
                    "batch evaluation_config 未提供可解析的 minimum_route_score。",
                    route_profile_key=snapshot.batch.route_profile_key,
                )
            )

        for route in snapshot.routes:
            _check_route_identity(snapshot, route, issues)
            _check_route_snapshot_fields(snapshot, route, issues)
            evidence = route.evidence

            # Anchor the pair with the persisted route.metric_id, then derive
            # the other scope(s) from the complete batch metric set.  The
            # evidence metric_ids are checked against this independently
            # reconstructed map and are never used as the source of truth.
            primary = metrics_by_id.get(route.metric_id)
            if primary is None:
                issues.append(
                    _blocked(
                        "ROUTE_PRIMARY_METRIC_MISSING",
                        "route.metric_id 在同一 batch metric 集合中不存在。",
                        route.factor_ref,
                        route_id=route.id,
                        metric_id=route.metric_id,
                    )
                )
                continue
            if not _same_route_metric_identity(route, primary):
                issues.append(
                    _failed(
                        "ROUTE_PRIMARY_METRIC_IDENTITY_MISMATCH",
                        "route.metric_id 指向的 metric 与 route 完整业务身份不一致。",
                        route.factor_ref,
                        route_id=route.id,
                        metric_id=route.metric_id,
                    )
                )
            pair_key = _metric_pair_key(primary)
            pair = metric_pairs.get(pair_key, {})
            if not pair:
                issues.append(
                    _blocked(
                        "ROUTE_METRIC_PAIR_MISSING",
                        "无法从同一 batch 的完整 metric 集合恢复 route.metric_id 所属 TS/CS pair。",
                        route.factor_ref,
                        route_id=route.id,
                        metric_id=route.metric_id,
                    )
                )
                continue
            for scope, metric in pair.items():
                if not _same_route_metric_identity(route, metric):
                    issues.append(
                        _failed(
                            "ROUTE_METRIC_PAIR_IDENTITY_MISMATCH",
                            "从完整 metric 集合恢复的 TS/CS metric 与 route 业务身份不一致。",
                            route.factor_ref,
                            route_id=route.id,
                            metric_id=metric.id,
                            scope=scope,
                        )
                    )

            valid_scopes = [
                scope
                for scope in _SCOPES
                if scope in pair and _metric_is_valid(pair[scope])
            ]
            # Reconcile both persisted route score columns independently of
            # route.metric_id.  In particular, a stale score from an invalid
            # scope must not be retained merely because the other scope is
            # valid.
            for scope in _SCOPES:
                metric = pair.get(scope)
                expected_scope_score: Decimal | None = None
                if metric is not None and _metric_is_valid(metric):
                    expected_scope_score = (
                        metric.time_series_score
                        if scope == "time_series"
                        else metric.cross_sectional_score
                    )
                _compare_route_scope_score(
                    expected_scope_score,
                    route.time_series_score if scope == "time_series" else route.cross_sectional_score,
                    issues,
                    route,
                    scope,
                )
            expected_metric_ids = {scope: pair[scope].id for scope in valid_scopes}
            if route.metric_id not in set(expected_metric_ids.values()):
                issues.append(
                    _failed(
                        "ROUTE_PRIMARY_METRIC_NOT_VALID",
                        "route.metric_id 未指向独立恢复出的有效 TS/CS metric。",
                        route.factor_ref,
                        route_id=route.id,
                        metric_id=route.metric_id,
                        expected_metric_ids=expected_metric_ids,
                    )
                )

            metric_ids = evidence.get("metric_ids") if isinstance(evidence, Mapping) else None
            actual_metric_ids: dict[str, int] = {}
            metric_ids_parseable = True
            if not isinstance(metric_ids, Mapping) or not metric_ids:
                issues.append(
                    _blocked(
                        "ROUTE_METRIC_IDS_MISSING",
                        "route evidence 缺少 TS/CS metric_ids。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            else:
                for scope, raw_id in metric_ids.items():
                    if scope not in _SCOPES:
                        metric_ids_parseable = False
                        issues.append(
                            _failed(
                                "ROUTE_METRIC_SCOPE_UNKNOWN",
                                "route evidence 包含未知 metric scope。",
                                route.factor_ref,
                                route_id=route.id,
                                scope=scope,
                            )
                        )
                        continue
                    metric_id = _plain_int(raw_id)
                    if metric_id is None:
                        metric_ids_parseable = False
                        issues.append(
                            _blocked(
                                "ROUTE_METRIC_ID_INVALID",
                                "route evidence.metric_ids 中存在不可解析的 metric ID。",
                                route.factor_ref,
                                route_id=route.id,
                                scope=scope,
                            )
                        )
                        continue
                    actual_metric_ids[scope] = metric_id
                if metric_ids_parseable and actual_metric_ids != expected_metric_ids:
                    issues.append(
                        _failed(
                            "ROUTE_METRIC_IDS_MISMATCH",
                            "route evidence.metric_ids 与完整 metric 集合独立恢复的有效 pair 不一致。",
                            route.factor_ref,
                            route_id=route.id,
                            expected=expected_metric_ids,
                            actual=actual_metric_ids,
                        )
                    )

            if not valid_scopes:
                issues.append(
                    _blocked(
                        "ROUTE_VALID_SCOPE_MISSING",
                        "route 没有 success 且 is_valid=true 的可评分维度。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
                continue
            expected_valid_scopes = set(valid_scopes)
            expected_invalid_scopes = set(_SCOPES) - expected_valid_scopes
            admission_mode = evidence.get("admission_mode")
            if admission_mode is None:
                issues.append(
                    _blocked(
                        "ROUTE_ADMISSION_MODE_MISSING",
                        "route evidence 缺少 admission_mode。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            elif admission_mode != "any_valid_scope":
                issues.append(
                    _failed(
                        "ROUTE_ADMISSION_MODE_MISMATCH",
                        "route evidence 的 admission_mode 不是 any_valid_scope。",
                        route.factor_ref,
                        route_id=route.id,
                        actual=admission_mode,
                    )
                )
            for field_name, expected_scopes in (
                ("valid_scopes", expected_valid_scopes),
                ("invalid_scopes", expected_invalid_scopes),
            ):
                raw_scopes = evidence.get(field_name)
                actual_scopes = _as_string_set(raw_scopes)
                if actual_scopes is None:
                    issues.append(
                        _blocked(
                            "ROUTE_SCOPE_EVIDENCE_MISSING",
                            f"route evidence 缺少合法的 {field_name} 列表。",
                            route.factor_ref,
                            route_id=route.id,
                            field=field_name,
                        )
                    )
                elif actual_scopes != expected_scopes or len(raw_scopes) != len(actual_scopes):
                    issues.append(
                        _failed(
                            "ROUTE_SCOPE_EVIDENCE_MISMATCH",
                            f"route evidence 的 {field_name} 与 metric 真值不一致。",
                            route.factor_ref,
                            route_id=route.id,
                            field=field_name,
                            expected=sorted(expected_scopes),
                            actual=raw_scopes,
                        )
                    )
            # The immutable batch configuration is the only source of the
            # configured profile weights.  Route evidence is an output to
            # reconcile, never a fallback when the batch config is absent.
            configured = configured_weights
            effective = evidence.get("effective_profile_weights")
            if configured is None:
                # A batch-level finding was emitted above.  Do not let a
                # copied weight map in route evidence make this route appear
                # independently verifiable.
                continue
            if not isinstance(effective, Mapping):
                issues.append(
                    _blocked(
                        "ROUTE_WEIGHT_CONFIG_MISSING",
                        "route evidence 缺少 effective profile weights。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
                continue
            configured_decimals = {scope: _to_decimal(configured.get(scope)) for scope in _SCOPES}
            if any(configured_decimals[scope] is None for scope in _SCOPES):
                issues.append(
                    _blocked(
                        "ROUTE_CONFIGURED_WEIGHT_INVALID",
                        "configured profile weight 不是完整 Decimal。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
                continue
            evidence_configured = evidence.get("configured_profile_weights")
            if not isinstance(evidence_configured, Mapping):
                issues.append(
                    _blocked(
                        "ROUTE_WEIGHT_CONFIG_MISSING",
                        "route evidence 缺少 configured profile weights。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            else:
                for scope in _SCOPES:
                    evidence_weight = _to_decimal(evidence_configured.get(scope))
                    if evidence_weight is None:
                        issues.append(
                            _blocked(
                                "ROUTE_CONFIGURED_WEIGHT_INVALID",
                                "route evidence 的 configured profile weight 不是完整 Decimal。",
                                route.factor_ref,
                                route_id=route.id,
                                scope=scope,
                            )
                        )
                    elif not _matches_persisted_scale(configured_decimals[scope], evidence_weight):
                        issues.append(
                            _failed(
                                "ROUTE_CONFIGURED_WEIGHT_MISMATCH",
                                "route evidence 的 configured profile weight 与 batch evaluation_config 不一致。",
                                route.factor_ref,
                                route_id=route.id,
                                scope=scope,
                                expected=str(configured_decimals[scope]),
                                actual=str(evidence_weight),
                            )
                        )
            valid_weight_sum = sum(
                (configured_decimals[scope] for scope in valid_scopes), Decimal(0)  # type: ignore[arg-type]
            )
            if valid_weight_sum <= 0:
                issues.append(
                    _blocked(
                        "ROUTE_VALID_WEIGHT_SUM_INVALID",
                        "有效维度的 configured weight 总和不为正数。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
                continue
            expected_weights = {
                scope: (
                    configured_decimals[scope] / valid_weight_sum  # type: ignore[operator]
                    if scope in valid_scopes else Decimal(0)
                )
                for scope in _SCOPES
            }
            for scope in _SCOPES:
                stored_weight = _to_decimal(effective.get(scope))
                if stored_weight is None:
                    issues.append(
                        _blocked(
                            "ROUTE_EFFECTIVE_WEIGHT_INVALID",
                            "effective profile weight 缺失或不是 Decimal。",
                            route.factor_ref,
                            route_id=route.id,
                            scope=scope,
                        )
                    )
                elif not _matches_persisted_scale(expected_weights[scope], stored_weight):
                    issues.append(
                        _failed(
                            "ROUTE_EFFECTIVE_WEIGHT_MISMATCH",
                            "有效维度权重重新归一化结果不一致。",
                            route.factor_ref,
                            route_id=route.id,
                            scope=scope,
                            expected=str(expected_weights[scope]),
                            stored=str(stored_weight),
                        )
                    )

            scores: dict[str, Decimal] = {}
            confidences: dict[str, Decimal] = {}
            for scope in valid_scopes:
                metric = pair[scope]
                score = metric.time_series_score if scope == "time_series" else metric.cross_sectional_score
                confidence = metric.confidence
                if score is None or confidence is None:
                    issues.append(
                        _blocked(
                            "ROUTE_SCORE_COMPONENT_MISSING",
                            "有效 metric 缺少维度 score 或 confidence。",
                            route.factor_ref,
                            route_id=route.id,
                            metric_id=metric.id,
                        )
                    )
                    continue
                scores[scope] = score
                confidences[scope] = confidence
                _check_metric_direction(metric, issues, route.id)
                scope_evidence = evidence.get(scope)
                if not isinstance(scope_evidence, Mapping):
                    issues.append(
                        _blocked(
                            "ROUTE_SCOPE_EVIDENCE_DETAIL_MISSING",
                            "有效 metric scope 缺少 route evidence 明细对象。",
                            route.factor_ref,
                            route_id=route.id,
                            scope=scope,
                        )
                    )
                else:
                    _compare_scope_evidence_decimal(
                        score,
                        scope_evidence,
                        "metric_score",
                        issues,
                        "ROUTE_EVIDENCE_METRIC_SCORE_MISMATCH",
                        "route evidence 的 metric_score 与 metric 行不一致。",
                        route,
                        scope,
                    )
                    _compare_scope_evidence_decimal(
                        confidence,
                        scope_evidence,
                        "confidence",
                        issues,
                        "ROUTE_EVIDENCE_SCOPE_CONFIDENCE_MISMATCH",
                        "route evidence 的 scope confidence 与 metric 行不一致。",
                        route,
                        scope,
                    )
            if set(scores) != set(valid_scopes) or set(confidences) != set(valid_scopes):
                continue
            base_score = sum(
                (scores[scope] * expected_weights[scope] for scope in valid_scopes),
                Decimal(0),
            )
            confidence = sum(
                (confidences[scope] * expected_weights[scope] for scope in valid_scopes),
                Decimal(0),
            )
            routing_score = base_score * confidence
            _compare_required_decimal(base_score, evidence.get("base_score"), issues, "ROUTE_BASE_SCORE_MISMATCH", route)
            _compare_required_decimal(confidence, evidence.get("confidence"), issues, "ROUTE_CONFIDENCE_MISMATCH", route)
            _compare_required_decimal(routing_score, evidence.get("routing_score"), issues, "ROUTE_EVIDENCE_SCORE_MISMATCH", route)
            if not _matches_persisted_scale(routing_score, route.routing_score):
                issues.append(
                    _failed(
                        "ROUTE_STORED_SCORE_MISMATCH",
                        "按有效维度、权重和 confidence 重算的 routing_score 与 route 不一致。",
                        route.factor_ref,
                        route_id=route.id,
                        calculated=str(routing_score),
                        stored=str(route.routing_score),
                    )
                )
            if route.confidence is None:
                issues.append(
                    _blocked(
                        "ROUTE_STORED_CONFIDENCE_MISSING",
                        "route 行缺少 confidence。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            elif not _matches_persisted_scale(confidence, route.confidence):
                issues.append(
                    _failed(
                        "ROUTE_STORED_CONFIDENCE_MISMATCH",
                        "重算 confidence 与 route 行不一致。",
                        route.factor_ref,
                        route_id=route.id,
                        calculated=str(confidence),
                        stored=str(route.confidence),
                    )
                )
            _check_route_admission_fields(
                route,
                valid_scopes=valid_scopes,
                independently_calculated_score=routing_score,
                minimum_route_score=minimum_route_score,
                issues=issues,
            )

        return _result(
            "CALC-506-A",
            "路由得分、方向与权重独立重算",
            len(snapshot.routes),
            issues,
            rounding="ROUND_HALF_UP",
            decimal_comparison="quantize_to_persisted_scale",
        )

    def check_route_environment_references(
        self,
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Check route references against one frozen batch/environment snapshot.

        Parameters
        ----------
        snapshot:
            A coherent read-only :class:`CalculationAuditSnapshot` containing
            the published batch, its routes, and the batch metric set.

        Returns
        -------
        CalculationCheckResult
            ``FAIL`` is returned for an explicit route/metric/batch identity
            contradiction or a route date that is explicitly marked missing
            by a snapshot whose date semantics are declared.  Missing
            snapshot members, malformed reference data, or unavailable
            daily evidence return ``BLOCKED_DATA_PRECONDITION``.  When the
            documents do not define whether ``route.environment_date`` is a
            snapshot member date or a publication-effective date, a
            ``BLOCKED_DOC`` observation is retained instead of guessing.

        Raises
        ------
        TypeError
            If ``snapshot`` is not a ``CalculationAuditSnapshot``.

        The method performs no network or database I/O and never treats a
        route's own evidence JSON as an identity authority.
        """

        if not isinstance(snapshot, CalculationAuditSnapshot):
            raise TypeError("snapshot must be a CalculationAuditSnapshot")

        batch = snapshot.batch
        routes = tuple(snapshot.routes)
        issues: list[CalculationIssue] = []
        if not routes:
            issues.append(
                _blocked(
                    "ROUTE_REFERENCE_SAMPLE_MISSING",
                    "当前发布没有可核对环境快照引用的 active route 样本。",
                )
            )

        metrics_by_id: dict[int, EvaluationMetric] = {}
        duplicate_metric_ids: set[int] = set()
        for metric in snapshot.evaluation_metrics:
            metric_id = getattr(metric, "id", None)
            if isinstance(metric_id, bool) or not isinstance(metric_id, int):
                issues.append(
                    _blocked(
                        "ROUTE_METRIC_ID_INVALID",
                        "metric 集合包含不可作为外键的 metric ID。",
                    )
                )
                continue
            if metric_id in metrics_by_id:
                duplicate_metric_ids.add(metric_id)
            else:
                metrics_by_id[metric_id] = metric
        if duplicate_metric_ids:
            issues.append(
                _failed(
                    "ROUTE_METRIC_ID_DUPLICATE",
                    "同一批次存在重复 metric ID，route 外键无法唯一解析。",
                    count=len(duplicate_metric_ids),
                    metric_id_samples=sorted(duplicate_metric_ids)[:_ISSUE_SAMPLE_LIMIT],
                )
            )

        factor_versions, factor_snapshot_errors = _factor_snapshot_versions(
            batch.factor_set_snapshot
        )
        if factor_snapshot_errors:
            # A malformed/missing snapshot is a data precondition.  Two
            # different versions for the same factor, however, are an
            # explicit contradiction in a supposedly immutable publication
            # and must remain a product failure rather than being hidden as a
            # missing prerequisite.
            if _FACTOR_SNAPSHOT_VERSION_CONFLICT_REASON in factor_snapshot_errors:
                issues.append(
                    _failed(
                        "ROUTE_FACTOR_SNAPSHOT_VERSION_CONFLICT",
                        "batch 冻结因子快照对同一 factor_ref 声明了互相冲突的版本。",
                        reason=_FACTOR_SNAPSHOT_VERSION_CONFLICT_REASON,
                    )
                )
            malformed_reasons = tuple(
                reason
                for reason in factor_snapshot_errors
                if reason != _FACTOR_SNAPSHOT_VERSION_CONFLICT_REASON
            )
            if malformed_reasons:
                issues.append(
                    _blocked(
                        "ROUTE_FACTOR_SNAPSHOT_MISSING",
                        "batch 缺少可解析的冻结因子成员版本，不能核对 route 因子引用。",
                        reason=malformed_reasons[0],
                        invalid_reasons=list(malformed_reasons),
                    )
                )

        environment_snapshot = getattr(batch, "environment_snapshot", None)
        environment_members: tuple[Mapping[str, Any], ...] = ()
        environment_member_dates: set[date] = set()
        environment_member_business_keys: list[tuple[date, str]] = []
        environment_member_ids: list[int] = []
        missing_dates: set[date] = set()
        missing_dates_valid = True
        route_date_semantics: str | None = None
        snapshot_as_of: datetime | None = None
        environment_daily_record_count = 0
        environment_daily_missing_id_count = 0
        if environment_snapshot is None:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_SNAPSHOT_MISSING",
                    "batch 未提供 environment_snapshot 正文，不能核对 daily 成员和 missing_dates。",
                )
            )
        elif not isinstance(environment_snapshot, Mapping):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_SNAPSHOT_INVALID",
                    "batch.environment_snapshot 不是 JSON 对象。",
                )
            )
        else:
            raw_members = environment_snapshot.get("members")
            if raw_members is None:
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBERS_MISSING",
                        "environment_snapshot 缺少 members，不能核对 route 日期引用。",
                    )
                )
            elif not isinstance(raw_members, Sequence) or isinstance(raw_members, (str, bytes)):
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBERS_INVALID",
                        "environment_snapshot.members 不是 JSON 数组。",
                    )
                )
            else:
                parsed_members: list[Mapping[str, Any]] = []
                for member in raw_members:
                    if not isinstance(member, Mapping):
                        issues.append(
                            _blocked(
                                "ROUTE_ENVIRONMENT_MEMBER_INVALID",
                                "environment_snapshot.members 含有非对象成员。",
                            )
                        )
                        continue
                    parsed_members.append(member)
                    raw_daily_id = member.get("daily_id")
                    if (
                        isinstance(raw_daily_id, bool)
                        or not isinstance(raw_daily_id, int)
                        or raw_daily_id < 1
                    ):
                        issues.append(
                            _blocked(
                                "ROUTE_ENVIRONMENT_MEMBER_ID_INVALID",
                                "environment_snapshot 成员缺少有效 daily_id。",
                            )
                        )
                    else:
                        environment_member_ids.append(raw_daily_id)
                    member_date = _snapshot_date(member.get("environment_date"))
                    if member_date is None:
                        issues.append(
                            _blocked(
                                "ROUTE_ENVIRONMENT_MEMBER_DATE_INVALID",
                                "environment_snapshot 成员缺少有效 environment_date。",
                            )
                        )
                    else:
                        environment_member_dates.add(member_date)
                        member_label_kind = member.get("label_kind", batch.label_kind)
                        if not isinstance(member_label_kind, str) or not member_label_kind.strip():
                            issues.append(
                                _blocked(
                                    "ROUTE_ENVIRONMENT_MEMBER_LABEL_KIND_INVALID",
                                    "environment_snapshot 成员 label_kind 不是有效字符串，不能建立 daily 业务键。",
                                )
                            )
                        else:
                            environment_member_business_keys.append(
                                (member_date, member_label_kind.strip())
                            )
                environment_members = tuple(parsed_members)
                if routes and not environment_members:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_MEMBERS_EMPTY",
                            "当前发布存在 active route，但 environment_snapshot.members 为空，不能回放环境引用。",
                        )
                    )

            duplicate_member_ids = len(environment_member_ids) - len(
                set(environment_member_ids)
            )
            if duplicate_member_ids:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_MEMBER_ID_DUPLICATE",
                        "environment_snapshot 成员 daily_id 不唯一。",
                        count=duplicate_member_ids,
                    )
                )

            duplicate_member_keys = {
                key for key in environment_member_business_keys
                if environment_member_business_keys.count(key) > 1
            }
            if duplicate_member_keys:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_MEMBER_BUSINESS_KEY_DUPLICATE",
                        "environment_snapshot 对同一 environment_date/label_kind 声明了多个成员，无法唯一选择 revision。",
                        count=len(duplicate_member_keys),
                        business_key_samples=[
                            {
                                "environment_date": key[0].isoformat(),
                                "label_kind": key[1],
                            }
                            for key in sorted(duplicate_member_keys)[:_ISSUE_SAMPLE_LIMIT]
                        ],
                    )
                )

            # An omitted field means that the producer supplied no explicit
            # missing-date list.  An explicit JSON null is different: it is a
            # malformed contract value and must not be silently converted to
            # an empty list (which could make a route appear valid).
            if "missing_dates" not in environment_snapshot:
                raw_missing_dates: Any = ()
            else:
                raw_missing_dates = environment_snapshot["missing_dates"]
            if raw_missing_dates is None or not isinstance(raw_missing_dates, Sequence) or isinstance(
                raw_missing_dates, (str, bytes)
            ):
                missing_dates_valid = False
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MISSING_DATES_INVALID",
                        "environment_snapshot.missing_dates 必须是 JSON 数组；显式 null 不能视为空数组。",
                    )
                )
            else:
                for raw_date in raw_missing_dates:
                    parsed_date = _snapshot_date(raw_date)
                    if parsed_date is None:
                        missing_dates_valid = False
                        issues.append(
                            _blocked(
                                "ROUTE_ENVIRONMENT_MISSING_DATE_INVALID",
                                "environment_snapshot.missing_dates 含有不可解析日期。",
                            )
                        )
                    else:
                        missing_dates.add(parsed_date)

            overlap = sorted(environment_member_dates & missing_dates)
            if overlap:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_MEMBER_IN_MISSING_SET",
                        "environment_snapshot 同时把成员日期列为 missing_dates。",
                        count=len(overlap),
                        date_samples=[value.isoformat() for value in overlap[:_ISSUE_SAMPLE_LIMIT]],
                    )
                )

            for field_name, expected_value in (
                ("market_scope", batch.market_scope),
                ("label_kind", batch.label_kind),
            ):
                actual_value = environment_snapshot.get(field_name)
                if actual_value is not None and actual_value != expected_value:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_SNAPSHOT_IDENTITY_MISMATCH",
                            "environment_snapshot 的 scope/label 身份与 batch 不一致。",
                            field=field_name,
                            expected=expected_value,
                            actual=actual_value,
                        )
                    )

            for field_name, expected_value in (
                ("start_date", batch.start_date),
                ("end_date", batch.end_date),
            ):
                actual_value = environment_snapshot.get(field_name)
                if actual_value is None:
                    continue
                parsed_value = _snapshot_date(actual_value)
                if parsed_value is None:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_SNAPSHOT_DATE_INVALID",
                            f"environment_snapshot.{field_name} 不是有效日期。",
                            field=field_name,
                        )
                    )
                elif isinstance(expected_value, date) and parsed_value != expected_value:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_SNAPSHOT_DATE_MISMATCH",
                            f"environment_snapshot.{field_name} 与 batch 日期不一致。",
                            field=field_name,
                            expected=expected_value.isoformat(),
                            actual=parsed_value.isoformat(),
                        )
                    )

            route_date_semantics = _route_environment_date_semantics(environment_snapshot)
            if route_date_semantics is None:
                issues.append(
                    _blocked_doc(
                        "ROUTE_ENVIRONMENT_DATE_SEMANTICS_UNDEFINED",
                        "文档和快照未定义 route.environment_date 是 snapshot 成员日还是发布生效日；日期差异仅作观察。",
                    )
                )

            raw_snapshot_as_of = environment_snapshot.get("as_of_time")
            if raw_snapshot_as_of is not None:
                snapshot_as_of = _snapshot_datetime(raw_snapshot_as_of)
                if snapshot_as_of is None:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_INVALID",
                            "environment_snapshot.as_of_time 不可解析。",
                        )
                    )
                elif isinstance(batch.as_of_time, datetime):
                    timestamp_match = _same_timestamp_instant(
                        snapshot_as_of,
                        batch.as_of_time,
                    )
                    if timestamp_match is None:
                        issues.append(
                            _blocked_doc(
                                "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_TIMEZONE_UNDEFINED",
                                "snapshot 与 DB batch 的 as_of_time 时区表示不同，且当前契约未声明 naive DB 时间的时区；暂不能裁决瞬时点。",
                            )
                        )
                    elif not timestamp_match:
                        issues.append(
                            _failed(
                                "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_MISMATCH",
                                "environment_snapshot.as_of_time 与 batch.as_of_time 不一致。",
                                expected_as_of_time=batch.as_of_time.isoformat(),
                                actual_as_of_time=snapshot_as_of.isoformat(),
                            )
                        )
            else:
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_SNAPSHOT_AS_OF_MISSING",
                        "environment_snapshot 缺少 as_of_time，不能确认环境可见性边界。",
                        )
                    )

        raw_environment_daily = getattr(snapshot, "environment_daily", ())
        if raw_environment_daily is None:
            environment_daily_records: tuple[Any, ...] = ()
        elif not isinstance(raw_environment_daily, Sequence) or isinstance(
            raw_environment_daily,
            (str, bytes),
        ):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_COLLECTION_INVALID",
                    "Repository 返回的 environment_daily 不是记录数组。",
                )
            )
            environment_daily_records = ()
        else:
            environment_daily_records = tuple(raw_environment_daily)

        raw_environment_history = getattr(snapshot, "environment_daily_history", ())
        if raw_environment_history is None:
            environment_daily_history: tuple[Any, ...] = ()
        elif not isinstance(raw_environment_history, Sequence) or isinstance(
            raw_environment_history,
            (str, bytes),
        ):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_COLLECTION_INVALID",
                    "Repository 返回的 environment_daily_history 不是记录数组。",
                )
            )
            environment_daily_history = ()
        else:
            environment_daily_history = tuple(raw_environment_history)
        raw_environment_history_loaded = getattr(
            snapshot,
            "environment_daily_history_loaded",
            False,
        )
        if isinstance(raw_environment_history_loaded, bool):
            environment_daily_history_loaded = raw_environment_history_loaded
        else:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_LOADED_INVALID",
                    "Repository 返回的 environment_daily_history_loaded 不是布尔值。",
                    actual_type=type(raw_environment_history_loaded).__name__,
                )
            )
            environment_daily_history_loaded = False
        environment_member_id_set = set(environment_member_ids)
        environment_daily_record_count, environment_daily_missing_id_count = (
            _reconcile_environment_daily_records(
                batch,
                environment_members,
                environment_member_ids,
                environment_daily_records,
                issues,
                history=environment_daily_history,
                history_loaded=environment_daily_history_loaded,
            )
        )

        status_by_label = batch.environment_status
        if not isinstance(status_by_label, Mapping) or not status_by_label:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_STATUS_MISSING",
                    "batch 缺少可按 label_code 查找的 environment_status。",
                )
            )

        # ``route_count`` is a published summary field with an established
        # meaning: active and eligible routes for the exact batch/publication
        # and label partition.  Validate its shape and compare it only when
        # the producer actually declares the field; do not invent a status
        # enum or require optional counters that older snapshots omit.
        route_counts_by_label: dict[tuple[str, str], int] = defaultdict(int)
        for route in routes:
            if not (route.is_active and route.is_eligible):
                continue
            if (
                route.eval_batch_id == batch.id
                and route.publication_uid == batch.publication_uid
                and route.publish_version == batch.publish_version
                and route.market_scope == batch.market_scope
                and route.route_profile_key == batch.route_profile_key
            ):
                route_counts_by_label[(route.label_kind, route.label_code)] += 1

        if isinstance(status_by_label, Mapping):
            for raw_label_code, raw_status in status_by_label.items():
                if not isinstance(raw_label_code, str) or not raw_label_code.strip():
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_LABEL_INVALID",
                            "environment_status 含有空白或非字符串 label_code。",
                        )
                    )
                    continue
                label_code = raw_label_code.strip()
                if not isinstance(raw_status, Mapping):
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_ENTRY_INVALID",
                            "environment_status 的 label entry 不是 JSON 对象。",
                            label_code=label_code,
                        )
                    )
                    continue
                raw_status_value = raw_status.get("status")
                if raw_status_value is None:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_VALUE_MISSING",
                            "environment_status label entry 缺少 status；当前不能判断该环境分区是否完成。",
                            label_code=label_code,
                        )
                    )
                elif not isinstance(raw_status_value, str) or not raw_status_value.strip():
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_VALUE_INVALID",
                            "environment_status label entry 的 status 不是非空字符串。",
                            label_code=label_code,
                        )
                    )

                if "route_count" not in raw_status:
                    continue
                route_count = _plain_int(raw_status.get("route_count"))
                if route_count is None or route_count < 0:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_INVALID",
                            "environment_status.route_count 必须是非负整数。",
                            label_code=label_code,
                        )
                    )
                    continue
                actual_route_count = route_counts_by_label.get(
                    (batch.label_kind, label_code),
                    0,
                )
                if route_count != actual_route_count:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_STATUS_ROUTE_COUNT_MISMATCH",
                            "发布摘要路由数量与实际有效路由数量不一致",
                            label_code=label_code,
                            expected_route_count=actual_route_count,
                            actual_route_count=route_count,
                        )
                    )

        # The database has a unique route-item key over these fields.  A
        # duplicate in the in-memory publication is therefore a concrete
        # identity violation, even if the rows happen to carry different IDs.
        route_business_keys: dict[tuple[Any, ...], int] = defaultdict(int)
        for route in routes:
            key = (
                route.publish_version,
                route.market_scope,
                route.label_kind,
                route.label_code,
                route.factor_ref,
                route.factor_version,
            )
            route_business_keys[key] += 1
        duplicate_route_keys = [
            key for key, count in route_business_keys.items() if count > 1
        ]
        if duplicate_route_keys:
            issues.append(
                _failed(
                    "ROUTE_REFERENCE_BUSINESS_KEY_DUPLICATE",
                    "同一发布中存在重复 route 业务键，无法唯一确定推荐成员。",
                    count=len(duplicate_route_keys),
                    business_key_samples=[
                        {
                            "publish_version": key[0],
                            "market_scope": key[1],
                            "label_kind": key[2],
                            "label_code": key[3],
                            "factor_ref": key[4],
                            "factor_version": key[5],
                        }
                        for key in duplicate_route_keys[:_ISSUE_SAMPLE_LIMIT]
                    ],
                )
            )

        for route in routes:
            identity_mismatches = {
                field_name: {"expected": expected, "actual": actual}
                for field_name, actual, expected in (
                    ("eval_batch_id", route.eval_batch_id, batch.id),
                    ("publication_uid", route.publication_uid, batch.publication_uid),
                    ("publish_version", route.publish_version, batch.publish_version),
                    ("market_scope", route.market_scope, batch.market_scope),
                    ("route_profile_key", route.route_profile_key, batch.route_profile_key),
                    ("label_kind", route.label_kind, batch.label_kind),
                    ("score_rule_version", route.score_rule_version, batch.score_rule_version),
                )
                if actual != expected
            }
            if identity_mismatches:
                issues.append(
                    _failed(
                        "ROUTE_REFERENCE_BATCH_IDENTITY_MISMATCH",
                        "route 引用的批次、publication、scope、label 或 score rule 身份与 batch 不一致。",
                        route.factor_ref,
                        route_id=route.id,
                        mismatches=identity_mismatches,
                    )
                )

            route_as_of_time = getattr(route, "as_of_time", None)
            batch_as_of_time = getattr(batch, "as_of_time", None)
            if not isinstance(route_as_of_time, datetime):
                issues.append(
                    _blocked(
                        "ROUTE_REFERENCE_AS_OF_TIME_INVALID",
                        "route.as_of_time 不是有效时间戳，不能确认其引用的计算快照。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            elif not isinstance(batch_as_of_time, datetime):
                issues.append(
                    _blocked(
                        "ROUTE_REFERENCE_BATCH_AS_OF_TIME_INVALID",
                        "batch.as_of_time 不是有效时间戳，不能确认 route 的快照边界。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            else:
                route_as_of_match = _same_timestamp_instant(
                    route_as_of_time,
                    batch_as_of_time,
                )
                if route_as_of_match is None:
                    issues.append(
                        _blocked_doc(
                            "ROUTE_REFERENCE_AS_OF_TIMEZONE_UNDEFINED",
                            "route 与 batch 的 as_of_time 时区表示不同，当前契约未声明 naive 时间的时区。",
                            route.factor_ref,
                            route_id=route.id,
                        )
                    )
                elif not route_as_of_match:
                    issues.append(
                        _failed(
                            "ROUTE_REFERENCE_AS_OF_TIME_MISMATCH",
                            "route.as_of_time 与 batch.as_of_time 不一致。",
                            route.factor_ref,
                            route_id=route.id,
                            expected_as_of_time=batch_as_of_time.isoformat(),
                            actual_as_of_time=route_as_of_time.isoformat(),
                        )
                    )

            metric_id = route.metric_id
            if isinstance(metric_id, bool) or not isinstance(metric_id, int):
                issues.append(
                    _blocked(
                        "ROUTE_METRIC_FOREIGN_KEY_INVALID",
                        "route.metric_id 不是有效整数外键。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
                metric = None
            else:
                metric = metrics_by_id.get(metric_id)
                if metric is None:
                    issues.append(
                        _failed(
                            "ROUTE_METRIC_FOREIGN_KEY_MISSING",
                            "route.metric_id 在同一批次 metric 集合中不存在。",
                            route.factor_ref,
                            route_id=route.id,
                            metric_id=metric_id,
                        )
                    )
            if metric is not None and not _same_route_metric_identity(route, metric):
                issues.append(
                    _failed(
                        "ROUTE_METRIC_REFERENCE_MISMATCH",
                        "route.metric_id 指向的 metric 业务身份与 route 不一致。",
                        route.factor_ref,
                        route_id=route.id,
                        metric_id=metric.id,
                    )
                )

            if factor_versions is not None:
                expected_version = factor_versions.get(route.factor_ref)
                if expected_version is None:
                    issues.append(
                        _failed(
                            "ROUTE_FACTOR_SNAPSHOT_MEMBER_MISSING",
                            "route 因子不在 batch 冻结成员快照中。",
                            route.factor_ref,
                            route_id=route.id,
                            expected_factor_version=expected_version,
                            actual_factor_version=route.factor_version,
                        )
                    )
                elif metric is not None:
                    metric_identity = metric.metric_identity
                    definition_version = (
                        metric_identity.get("definition_factor_version")
                        if isinstance(metric_identity, Mapping)
                        else None
                    )
                    if not isinstance(definition_version, str) or not definition_version.strip():
                        issues.append(
                            _blocked(
                                "ROUTE_METRIC_DEFINITION_VERSION_MISSING",
                                "metric 缺少用于回放冻结成员的 definition_factor_version。",
                                route.factor_ref,
                                route_id=route.id,
                                metric_id=metric.id,
                            )
                        )
                    elif definition_version.strip() != expected_version:
                        issues.append(
                            _failed(
                                "ROUTE_FACTOR_SNAPSHOT_DEFINITION_VERSION_MISMATCH",
                                "metric 的 definition_factor_version 与 batch 冻结成员版本不一致。",
                                route.factor_ref,
                                route_id=route.id,
                                metric_id=metric.id,
                                expected_definition_factor_version=expected_version,
                                actual_definition_factor_version=definition_version,
                            )
                        )

            route_date = _snapshot_date(route.environment_date)
            if route_date is None:
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_DATE_INVALID",
                        "route.environment_date 不是有效日期。",
                        route.factor_ref,
                        route_id=route.id,
                    )
                )
            elif route_date_semantics == "snapshot_member":
                # When the member date set is empty, the earlier member/DB
                # precondition findings are the only defensible conclusion;
                # do not turn missing evidence into a spurious route FAIL.
                if environment_member_dates:
                    if missing_dates_valid and route_date in missing_dates:
                        issues.append(
                            _failed(
                                "ROUTE_ENVIRONMENT_DATE_IN_MISSING_SET",
                                "route.environment_date 命中了 environment_snapshot.missing_dates。",
                                route.factor_ref,
                                route_id=route.id,
                                environment_date=route_date.isoformat(),
                            )
                        )
                    if route_date not in environment_member_dates:
                        issues.append(
                            _failed(
                                "ROUTE_ENVIRONMENT_DATE_NOT_IN_SNAPSHOT",
                                "route.environment_date 不在冻结 environment_snapshot 成员日期中。",
                                route.factor_ref,
                                route_id=route.id,
                                environment_date=route_date.isoformat(),
                            )
                        )
                matching_daily_records = [
                    record
                    for record in environment_daily_records
                    if isinstance(record, EnvironmentDailyRecord)
                    and getattr(record, "id", None) in environment_member_id_set
                    and getattr(record, "environment_date", None) == route_date
                ]
                # A NULL/blank label_code is an incomplete daily identity,
                # already reported as a data-precondition above.  It cannot
                # prove that the route is contradictory, so do not turn that
                # absence of evidence into a second FAIL.  A complete but
                # different identity remains an explicit failure.
                complete_matching_records = [
                    record
                    for record in matching_daily_records
                    if isinstance(getattr(record, "label_kind", None), str)
                    and getattr(record, "label_kind", "").strip()
                    and isinstance(getattr(record, "label_code", None), str)
                    and getattr(record, "label_code", "").strip()
                ]
                if complete_matching_records and not any(
                    getattr(record, "label_kind", None) == route.label_kind
                    and getattr(record, "label_code", None) == route.label_code
                    for record in complete_matching_records
                ):
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_DAILY_IDENTITY_MISMATCH",
                            "route 的 environment_date/label_kind/label_code 未命中同一冻结 daily 成员。",
                            route.factor_ref,
                            route_id=route.id,
                            environment_date=route_date.isoformat(),
                            label_kind=route.label_kind,
                            label_code=route.label_code,
                            daily_id_samples=[
                                getattr(record, "id", None)
                                for record in matching_daily_records[:_ISSUE_SAMPLE_LIMIT]
                            ],
                        )
                    )
                if isinstance(batch.start_date, date) and route_date < batch.start_date:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_DATE_OUT_OF_BATCH_RANGE",
                            "snapshot 成员语义下 route.environment_date 早于 batch.start_date。",
                            route.factor_ref,
                            route_id=route.id,
                            environment_date=route_date.isoformat(),
                            start_date=batch.start_date.isoformat(),
                        )
                    )
                if isinstance(batch.end_date, date) and route_date > batch.end_date:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_DATE_OUT_OF_BATCH_RANGE",
                            "snapshot 成员语义下 route.environment_date 晚于 batch.end_date。",
                            route.factor_ref,
                            route_id=route.id,
                            environment_date=route_date.isoformat(),
                            end_date=batch.end_date.isoformat(),
                        )
                    )
            elif route_date_semantics == "publication_effective":
                if not isinstance(batch.end_date, date) or isinstance(
                    batch.end_date, datetime
                ):
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_BATCH_END_DATE_INVALID",
                            "publication_effective 语义需要 batch.end_date 才能核对 route 日期。",
                            route.factor_ref,
                            route_id=route.id,
                        )
                    )
                elif route_date != batch.end_date:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_DATE_MISMATCH",
                            "明确声明 publication_effective 语义时，route.environment_date 必须等于 batch.end_date。",
                            route.factor_ref,
                            route_id=route.id,
                            expected_environment_date=batch.end_date.isoformat(),
                            actual_environment_date=route_date.isoformat(),
                        )
                    )

            if isinstance(status_by_label, Mapping) and status_by_label:
                if route.label_code not in status_by_label:
                    issues.append(
                        _blocked(
                            "ROUTE_ENVIRONMENT_STATUS_LABEL_MISSING",
                            "route.label_code 在 batch.environment_status 中不存在。",
                            route.factor_ref,
                            route_id=route.id,
                            label_code=route.label_code,
                        )
                    )

        return _result(
            "CALC-513",
            "route 与环境快照引用完整性",
            len(routes),
            issues,
            route_count=len(routes),
            metric_count=len(snapshot.evaluation_metrics),
            environment_member_count=len(environment_members),
            environment_member_date_count=len(environment_member_dates),
            environment_daily_record_count=environment_daily_record_count,
            environment_daily_member_count=len(environment_member_id_set),
            environment_daily_missing_id_count=environment_daily_missing_id_count,
            environment_daily_history_count=len(environment_daily_history),
            environment_daily_history_loaded=environment_daily_history_loaded,
            missing_date_count=len(missing_dates),
            route_environment_date_semantics=route_date_semantics or "UNDEFINED",
            snapshot_as_of_time=snapshot_as_of.isoformat() if snapshot_as_of else None,
        )

    def check_rank_stability(
        self,
        snapshot: CalculationAuditSnapshot,
        repeated_snapshot: PublishedRouteSnapshot | CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """Check partitioned Decimal ranking and repeat-read stability.

        Ranks must start at one without gaps, scores must descend numerically,
        and factor/version identities must be unique in each full publication
        partition. The documented partition uses the calendar ``as_of_date``;
        each route's exact ``as_of_time`` must nevertheless equal the batch
        snapshot timestamp. A changed publication between reads is a
        data-precondition block. A stable and otherwise correct result remains
        ``BLOCKED_DOC`` because the current documents do not define a score-tie
        breaker. Frozen-member/metric drift is a structured ``FAIL`` finding.
        """

        result = self.check_final_result_ranking(snapshot, repeated_snapshot)
        issues = [*_membership_difference_issues(snapshot), *result.findings]
        if not issues:
            issues.append(
                CalculationIssue(
                    status="BLOCKED_DOC",
                    code="RANK_TIE_BREAKER_UNDEFINED",
                    message="三份 Factor 4.0 文档均未定义同分 tie-breaker，不能自行使用 factor ID。",
                )
            )
        return _result(
            "CALC-507-A",
            "排名分区与重复读取稳定性",
            result.checked_count,
            issues,
            **result.evidence,
        )

    @staticmethod
    def check_final_result_ranking(
        snapshot: CalculationAuditSnapshot,
        repeated_snapshot: PublishedRouteSnapshot | CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """核对已发布结果的排序，不要求原始序列或完整公式证据。

        输入两次只读结果快照；返回连续排名、分数降序、分区内唯一性及重复读取
        稳定性的结构化判定。发布切换返回数据前置阻断，明确矛盾返回失败；不执行
        I/O。同分只验证排名唯一和重复读取一致，不判定文档尚未规定的优先顺序。
        这不是独立计算重跑，也不验证输入指标本身是否计算正确。
        """

        issues: list[CalculationIssue] = []
        first_partitions = _route_partitions(snapshot.routes)
        second_partitions = _repeat_route_partitions(repeated_snapshot)
        _check_route_as_of_contract(
            snapshot.batch.as_of_time,
            snapshot.routes,
            issues,
            "first",
        )
        # A different publication is an independent snapshot and cannot be
        # judged against the first batch's timestamp.  Once publication
        # identity is stable, compare the repeated rows to that first batch
        # timestamp to catch same-day clock drift.
        if _publication_identity(snapshot) == _publication_identity(repeated_snapshot):
            _check_route_as_of_contract(
                snapshot.batch.as_of_time,
                repeated_snapshot.routes,
                issues,
                "repeated",
            )
        if not first_partitions:
            issues.append(_blocked("RANK_ROUTE_SAMPLE_MISSING", "当前发布没有 active eligible route 排名样本。"))
        _check_partition_ranks(first_partitions, issues, "first")
        _check_partition_ranks(second_partitions, issues, "repeated")

        if _publication_identity(snapshot) != _publication_identity(repeated_snapshot):
            issues.append(
                _blocked(
                    "RANK_PUBLICATION_CHANGED",
                    "两次读取之间 active publication 已变化，不能裁决稳定性。",
                    first_publication=snapshot.batch.publication_uid,
                    repeated_publication=(
                        repeated_snapshot.batch.publication_uid
                        if isinstance(repeated_snapshot, CalculationAuditSnapshot)
                        else repeated_snapshot.publication_uid
                    ),
                )
            )
        elif _partition_sequences(first_partitions) != _partition_sequences(second_partitions):
            issues.append(
                _failed(
                    "RANK_REPEAT_READ_UNSTABLE",
                    "同一 publication 的重复读取返回了不同的成员、rank、顺序或分数。",
                )
            )

        return _result(
            "RESULT-504",
            "已发布结果排序与稳定性",
            sum(len(routes) for routes in first_partitions.values()),
            issues,
            partition_count=len(first_partitions),
            validation_level="FINAL_RESULT_CONSISTENCY",
            tie_priority="NOT_VERIFIED_DOCUMENT_UNDEFINED",
            repeated_calculation=False,
            partition_key=(
                "eval_batch_id",
                "publication_uid",
                "publish_version",
                "market_scope",
                "route_profile_key",
                "label_kind",
                "label_code",
                "environment_date",
                "as_of_date",
            ),
        )

    def _reset_mcp_cache(self) -> None:
        self._detail_cache.clear()
        self._detail_errors.clear()
        self._formula_cache.clear()
        self._formula_errors.clear()
        self._mcp_cache_scope = None

    def _ensure_mcp_cache_scope(self, snapshot: CalculationAuditSnapshot) -> None:
        """Invalidate MCP projections when a different DB snapshot is checked.

        The public formula checks may be called independently of
        :meth:`run_r0_checks`.  A cache populated for one publication must not
        be reused for another publication, or for a snapshot whose formula
        evidence/detail inputs changed.  The scope is deliberately derived
        from the immutable input rows rather than from the MCP response, so a
        second check in the same run can still share bounded reads.
        """

        scope = _mcp_snapshot_scope(snapshot)
        if scope == self._mcp_cache_scope:
            return
        self._reset_mcp_cache()
        self._mcp_cache_scope = scope

    def _load_mcp_details(self, factor_refs: Sequence[str]) -> None:
        missing = sorted(
            {
                factor_ref
                for factor_ref in factor_refs
                if factor_ref not in self._detail_cache and factor_ref not in self._detail_errors
            }
        )
        for start in range(0, len(missing), 50):
            chunk = missing[start : start + 50]
            # Transport/protocol/RPC exceptions are deliberately allowed to
            # propagate.  They invalidate the whole batch-level report and are
            # not equivalent to a legitimate per-factor business miss.
            response = self._mcp_api.get_factor_details_batch(
                chunk,
                detail_level="executable",
            )
            if response.is_tool_error:
                error_code = _tool_error_code(response)
                for factor_ref in chunk:
                    self._detail_errors[factor_ref] = error_code
                # ``EXPORT_BUDGET_EXCEEDED`` is a server-side daily budget
                # exhaustion signal (not a per-request payload-size hint).
                # Stop issuing the same doomed request for every remaining
                # chunk; all of those refs are blocked by the same precondition.
                if error_code == _TERMINAL_MCP_DETAIL_ERROR:
                    for factor_ref in missing[start + len(chunk) :]:
                        self._detail_errors[factor_ref] = error_code
                    break
                continue
            items = _batch_detail_items(response)
            returned: set[str] = set()
            ambiguous: set[str] = set()
            for item in items:
                factor_ref = item.get("factor_ref")
                if not isinstance(factor_ref, str) or factor_ref not in chunk:
                    continue
                if factor_ref in returned:
                    # A batch response must contain at most one item per
                    # requested factor.  Keeping the first item would make a
                    # contradictory response depend on server ordering.
                    ambiguous.add(factor_ref)
                    self._detail_cache.pop(factor_ref, None)
                    self._detail_errors[factor_ref] = "MCP_BATCH_DUPLICATE_FACTOR_REF"
                    continue
                returned.add(factor_ref)
                data = item.get("data")
                if item.get("success") is True and isinstance(data, Mapping):
                    self._detail_cache[factor_ref] = dict(data)
                else:
                    error = item.get("error")
                    code = error.get("code") if isinstance(error, Mapping) else None
                    self._detail_errors[factor_ref] = str(code or "MCP_ITEM_ERROR")
            for factor_ref in set(chunk) - returned:
                self._detail_errors[factor_ref] = "MCP_BATCH_ITEM_MISSING"
            # ``ambiguous`` is intentionally kept separate from missing: the
            # response did mention the factor, but no single projection can be
            # trusted.  The error map is already populated above; this loop
            # documents the invariant and prevents a future refactor from
            # treating duplicate entries as successful data.
            for factor_ref in ambiguous:
                self._detail_cache.pop(factor_ref, None)
                self._detail_errors[factor_ref] = "MCP_BATCH_DUPLICATE_FACTOR_REF"

    def _load_mcp_formula(self, evidence: FormulaEvidence) -> None:
        key = _formula_key(evidence)
        if key in self._formula_cache or key in self._formula_errors:
            return
        # See ``_load_mcp_details``: only a declared tool-level business error
        # is cacheable as a data miss.  All transport and protocol exceptions
        # must remain visible to the caller.
        response = self._mcp_api.get_formula(
            evidence.factor_ref,
            evidence.run_id,
            evidence.factor_bar_interval,
            evidence.factor_window_bars,
            evidence.return_bar_interval,
            evidence.forward_return_bars,
            calculation_mode=evidence.calculation_mode,
        )
        if response.is_tool_error:
            self._formula_errors[key] = _tool_error_code(response)
            return
        data = _tool_data(response)
        if data is None:
            self._formula_errors[key] = "MCP_FORMULA_DATA_MISSING"
        else:
            self._formula_cache[key] = data


def _static_int(
    node: ast.AST,
    variables: Mapping[str, int] | None = None,
) -> int:
    """Resolve a bounded non-negative integer expression without evaluation.

    ``variables`` is intentionally a small, caller-supplied substitution map;
    arbitrary names remain dynamic and therefore raise ``FormulaOffsetError``.
    The bound also prevents an untrusted formula from forcing a huge offset
    expansion.
    """

    if isinstance(node, ast.Name):
        folded = node.id.casefold()
        for name, declared in (variables or {}).items():
            if str(name).casefold() == folded and isinstance(declared, int) and not isinstance(declared, bool):
                value = declared
                break
        else:
            raise FormulaOffsetError("temporal argument is dynamic")
    elif (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        value = node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _static_int(node.operand, variables)
        value = operand if isinstance(node.op, ast.UAdd) else -operand
    elif isinstance(node, ast.BinOp):
        left = _static_int(node.left, variables)
        right = _static_int(node.right, variables)
        if isinstance(node.op, ast.Add):
            value = left + right
        elif isinstance(node.op, ast.Sub):
            value = left - right
        elif isinstance(node.op, ast.Mult):
            value = left * right
        elif isinstance(node.op, ast.FloorDiv) and right != 0:
            value = left // right
        elif isinstance(node.op, ast.Div) and right != 0 and left % right == 0:
            value = left // right
        elif isinstance(node.op, ast.Mod) and right != 0:
            value = left % right
        else:
            raise FormulaOffsetError("temporal argument is not static integer arithmetic")
    else:
        raise FormulaOffsetError("temporal argument is dynamic")
    if value < 0:
        raise FormulaOffsetError("temporal argument must be non-negative")
    if value > _MAX_STATIC_PERIOD:
        raise FormulaOffsetError("temporal argument exceeds the supported bound")
    return value


def _static_number(node: ast.AST) -> int | float:
    """Resolve a finite numeric literal used by a static control argument."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        value = node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _static_number(node.operand)
        value = operand if isinstance(node.op, ast.UAdd) else -operand
    else:
        raise FormulaOffsetError("control argument is dynamic")
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        raise FormulaOffsetError("control argument must be finite")
    return value


def _static_literal(node: ast.AST) -> Any:
    """Resolve a literal control value; names are never evaluated."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, ast.Name) and node.id.casefold() in {"nan", "inf"}:
        return node.id.casefold()
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id.casefold() in {"np", "numpy", "pd", "pandas"}
        and node.attr.casefold() in {"nan", "inf", "nat", "na"}
    ):
        return f"{node.value.id.casefold()}.{node.attr.casefold()}"
    raise FormulaOffsetError("control argument is dynamic")


def _call_period(
    node: ast.Call,
    position: int,
    keywords: Sequence[str],
    default: int | None,
    variables: Mapping[str, int] | None = None,
) -> int:
    candidates: list[int] = []
    if len(node.args) > position:
        candidates.append(_static_int(node.args[position], variables))
    for keyword in node.keywords:
        if keyword.arg is not None and keyword.arg.casefold() in {
            value.casefold() for value in keywords
        }:
            candidates.append(_static_int(keyword.value, variables))
    if candidates:
        if len(candidates) > 1:
            raise FormulaOffsetError("temporal call has duplicate or conflicting period arguments")
        return candidates[0]
    if default is None:
        raise FormulaOffsetError("temporal call has no statically resolvable window")
    return default


def _validate_call_keywords(
    node: ast.Call,
    operation: str,
    variables: Mapping[str, int] | None = None,
) -> None:
    """Validate keyword names and static control values for one call."""

    allowed = _FUNCTION_KEYWORDS.get(operation, frozenset())
    seen: set[str] = set()
    for keyword in node.keywords:
        if keyword.arg is None:
            raise FormulaOffsetError("variadic keyword arguments are unsupported")
        name = keyword.arg.casefold()
        if name not in allowed:
            raise FormulaOffsetError(
                f"unknown keyword for {operation}: {keyword.arg}"
            )
        if name in seen:
            raise FormulaOffsetError(f"duplicate keyword for {operation}: {keyword.arg}")
        seen.add(name)
        if name in {"window", "period", "periods", "min_periods", "span", "halflife"}:
            _static_int(keyword.value, variables)
        elif name == "alpha":
            alpha = _static_number(keyword.value)
            if not 0 < float(alpha) <= 1:
                raise FormulaOffsetError("alpha must be in (0, 1]")
        elif name == "ddof":
            _static_int(keyword.value, variables)
        elif name in {"lower", "upper", "q"}:
            _static_number(keyword.value)
        elif name in {"limit", "decimals"}:
            _static_int(keyword.value, variables)
        elif name in _STATIC_CONTROL_KEYWORDS:
            _static_literal(keyword.value)


def _offset_window(
    base: set[int],
    period: int,
    *,
    operation: str,
) -> set[int]:
    """Expand a finite rolling window over an already resolved base series."""

    if period < 1:
        raise FormulaOffsetError(f"{operation} window must be positive")
    if not base:
        raise FormulaOffsetError(f"{operation} call has no base series")
    return {offset + lag for offset in base for lag in range(period)}


def _validate_trailing_rolling_window(
    node: ast.Call,
    period: int,
    variables: Mapping[str, int] | None,
) -> None:
    """Admit only rolling options whose trailing-row offsets are provable.

    The static oracle models a conventional trailing integer window.  Options
    such as ``center``, ``closed``, ``step``, ``on`` and ``win_type`` can move
    the window, introduce future rows, change output alignment, or even make a
    subsequent operation invalid.  They therefore fail closed until an
    explicit versioned semantic is implemented.  ``min_periods`` does not
    alter the maximum dependency set, but pandas requires it to be no greater
    than the window and the oracle enforces the same bound.
    """

    period_names = {"window", "period", "periods"}
    for keyword in node.keywords:
        if keyword.arg is None:
            raise FormulaOffsetError("variadic rolling keywords are unsupported")
        name = keyword.arg.casefold()
        if name in period_names:
            continue
        if name == "min_periods":
            min_periods = _static_int(keyword.value, variables)
            if min_periods > period:
                raise FormulaOffsetError("rolling min_periods cannot exceed window")
            continue
        raise FormulaOffsetError(
            f"rolling modifier is unsupported by the trailing-window oracle: {keyword.arg}"
        )


def _is_finite_rolling_corr_method(node: ast.Call) -> bool:
    """Return whether ``node`` is a bounded ``rolling(...).corr(...)`` chain.

    Only the immediate ``rolling`` receiver is accepted.  This deliberately
    excludes namespace calls (for example ``np.corr``), unbounded aggregate
    chains, and a second correlation layered on top of another correlation.
    """

    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr.casefold() == "corr"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Attribute)
        and node.func.value.func.attr.casefold() == "rolling"
    )


def _rolling_corr_method_offsets(
    node: ast.Call,
    base: set[int],
    variables: Mapping[str, int] | None,
) -> set[int]:
    """Resolve dependencies for ``series.rolling(window).corr(other)``.

    Pandas computes correlation over the rolling window for both operands.  A
    plain recursive walk would expand only the receiver and under-report the
    second operand's lookback (for example ``x.pct_change(24)``).  The helper
    therefore expands the second operand over the same finite window.  Method
    keyword arguments are intentionally unsupported until their semantics are
    versioned; rejecting them avoids silently ignoring ``pairwise``/``ddof``.
    """

    if not _is_finite_rolling_corr_method(node):
        raise FormulaOffsetError("qualified correlation requires a finite rolling receiver")
    if node.keywords:
        raise FormulaOffsetError("rolling correlation method keywords are unsupported")
    if len(node.args) != 1:
        raise FormulaOffsetError("rolling correlation requires exactly one other series")
    rolling = node.func.value
    period = _call_period(
        rolling,
        0,
        ("window", "period", "periods"),
        None,
        variables,
    )
    other = _dependency_offsets(node.args[0], variables)
    return base | _offset_window(other, period, operation="corr")


def _windowed_function_offsets(
    node: ast.Call,
    operation: str,
    variables: Mapping[str, int] | None,
) -> set[int]:
    """Resolve common functional rolling helpers with explicit finite windows."""

    args = node.args
    # ``rolling_vwap(24)``/``vwap(24)`` are catalog helpers whose first
    # argument is the implicit window.  Their underlying OHLCV inputs are not
    # represented in the expression, so the conservative dependency is the
    # full finite lookback rather than an empty set.
    if operation in {"vwap", "rolling_vwap"} and len(args) == 1:
        try:
            period = _static_int(args[0], variables)
        except FormulaOffsetError:
            period = None
        if period is not None:
            if any(
                keyword.arg is not None
                and keyword.arg.casefold() in {"window", "period", "periods"}
                for keyword in node.keywords
            ):
                raise FormulaOffsetError("temporal call has conflicting period arguments")
            return set(range(period))

    if operation in {"vwap", "rolling_vwap"}:
        # Explicit-data forms are accepted as either ``vwap(series, window)``
        # or ``vwap(price, volume, window)``.  A named window may replace the
        # final positional argument.  In every form the window must be
        # statically resolvable; otherwise the helper would under-report its
        # lookback.
        period_keyword_present = any(
            keyword.arg is not None
            and keyword.arg.casefold() in {"window", "period", "periods"}
            for keyword in node.keywords
        )
        if period_keyword_present:
            if len(args) not in {1, 2}:
                raise FormulaOffsetError(f"{operation} has too many positional arguments")
            data_args = args
            period_position = len(args)
        elif len(args) == 2:
            try:
                _static_int(args[1], variables)
            except FormulaOffsetError:
                raise FormulaOffsetError(
                    f"{operation} has no statically resolvable window"
                ) from None
            data_args = args[:1]
            period_position = 1
        elif len(args) == 3:
            data_args = args[:2]
            period_position = 2
        else:
            raise FormulaOffsetError(f"{operation} call has no base series")
        base = set().union(*(_dependency_offsets(arg, variables) for arg in data_args))
        period = _call_period(
            node,
            period_position,
            ("window", "period", "periods"),
            None,
            variables,
        )
        return _offset_window(base, period, operation=operation)

    if operation in {"correlation", "corr"}:
        required_data = 2
        period_position = 2
    elif operation == "atr":
        required_data = 3
        period_position = 3
    else:
        required_data = 1
        period_position = 1
    if len(args) < required_data:
        raise FormulaOffsetError(f"{operation} call has no base series")
    max_args = 4 if operation == "atr" else 3 if operation in {"correlation", "corr"} else 2
    if len(args) > max_args:
        raise FormulaOffsetError(f"{operation} has too many positional arguments")
    base = set().union(*(_dependency_offsets(arg, variables) for arg in args[:required_data]))
    period = _call_period(
        node,
        period_position,
        ("window", "period", "periods"),
        None,
        variables,
    )
    return _offset_window(base, period, operation=operation)


def _attribute_root_name(node: ast.AST) -> str | None:
    """Return the root identifier of an attribute chain.

    ``node`` is an AST value such as ``np`` or ``pandas.Series``.  The return
    value is the case-folded root name, or ``None`` when the chain starts with
    a non-name expression.  No expression is evaluated.
    """

    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id.casefold()
    return None


def _is_derived_aggregate_method(node: ast.Call, operation: str) -> bool:
    """Return whether an aggregate consumes an immediate finite rolling object.

    Method-style chains such as ``close.rolling(24).mean()`` are already
    resolved by the inner temporal call.  The outer aggregate contributes no
    additional lookback only when it has no positional arguments and uses an
    offset-neutral keyword supported by the corresponding pandas Rolling
    method.  A direct ``close.mean(...)``, ``close.diff().mean()``, or a
    qualified ``np.mean(...)`` must not use this compatibility path because it
    aggregates an unbounded series or has ambiguous window semantics.
    """

    if operation not in {"max", "mean", "median", "min", "std", "sum"}:
        return False
    if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Call):
        return False
    receiver = node.func.value
    if (
        not isinstance(receiver.func, ast.Attribute)
        or receiver.func.attr.casefold() != "rolling"
    ):
        return False
    if node.args:
        return False
    allowed_keywords = {"numeric_only"}
    if operation == "std":
        allowed_keywords.add("ddof")
    return all(
        keyword.arg is not None and keyword.arg.casefold() in allowed_keywords
        for keyword in node.keywords
    )


def _validate_qualified_call(node: ast.Call, operation: str) -> None:
    """Reject qualified temporal calls whose lookback cannot be proven.

    ``np``/``pd`` namespace functions are accepted only for a closed set of
    element-wise mathematical operations.  Temporal namespace calls and
    unsupported methods fail closed; known method chains with explicit logic
    are handled by ``_dependency_offsets`` after this guard.
    """

    if not isinstance(node.func, ast.Attribute):
        return
    if operation in _UNBOUNDED_TEMPORAL_NAMES_CASEFOLD:
        raise FormulaOffsetError(
            f"unbounded temporal operation is unsupported: {operation}"
        )
    if operation == "corr" and _is_finite_rolling_corr_method(node):
        return
    if operation in _QUALIFIED_TEMPORAL_NAMES_CASEFOLD:
        if operation in {"diff", "pct_change", "rolling", "shift"}:
            # These four have a fully specified method-style implementation
            # below.  Namespace forms (np.shift, pd.rolling, ...) are still
            # rejected because their positional semantics differ.
            root = _attribute_root_name(node.func.value)
            if root in _QUALIFIED_NAMESPACE_ROOTS:
                raise FormulaOffsetError(
                    f"qualified temporal operation is unsupported: {operation}"
                )
            return
        if _is_derived_aggregate_method(node, operation):
            return
        raise FormulaOffsetError(
            f"qualified temporal operation is unsupported: {operation}"
        )
    root = _attribute_root_name(node.func.value)
    if root in _QUALIFIED_NAMESPACE_ROOTS and operation not in _QUALIFIED_NAMESPACE_SAFE_NAMES:
        raise FormulaOffsetError(
            f"qualified namespace operation is unsupported: {operation}"
        )


def _dependency_offsets(
    node: ast.AST,
    variables: Mapping[str, int] | None = None,
) -> set[int]:
    """Resolve supported formula AST nodes, rejecting unknown semantics."""

    if isinstance(node, ast.Expression):
        return _dependency_offsets(node.body, variables)
    if isinstance(node, ast.Name):
        # A bare name is presumed to be a current-period raw series unless it
        # is a known function/namespace or a formula-control constant.  This
        # keeps parameter names such as ``window`` from becoming fake input
        # fields while preserving the conservative offset-0 behavior for
        # actual catalog fields that are not in a hard-coded schema.
        if (
            node.id.casefold() in _NON_FIELD_NAMES_CASEFOLD
            or node.id.casefold() in _KNOWN_FUNCTION_NAMES_CASEFOLD
        ):
            return set()
        if node.id.startswith("__"):
            raise FormulaOffsetError("private formula names are unsupported")
        return {0}
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise FormulaOffsetError("formula literal type is unsupported")
        return set()
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub, ast.Not, ast.Invert)):
            raise FormulaOffsetError("unary operator is unsupported")
        return _dependency_offsets(node.operand, variables)
    if isinstance(node, ast.BinOp):
        if not isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
        ):
            raise FormulaOffsetError("binary operator is unsupported")
        return _dependency_offsets(node.left, variables) | _dependency_offsets(
            node.right, variables
        )
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise FormulaOffsetError("boolean operator is unsupported")
        return set().union(*(_dependency_offsets(value, variables) for value in node.values))
    if isinstance(node, ast.Compare):
        if any(
            not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn))
            for op in node.ops
        ):
            raise FormulaOffsetError("comparison operator is unsupported")
        return _dependency_offsets(node.left, variables) | set().union(
            *(_dependency_offsets(value, variables) for value in node.comparators)
        )
    if isinstance(node, (ast.IfExp, ast.NamedExpr, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise FormulaOffsetError(f"unsupported formula AST node: {type(node).__name__}")
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise FormulaOffsetError("private formula attributes are unsupported")
        return _dependency_offsets(node.value, variables)
    if isinstance(node, ast.Subscript):
        raise FormulaOffsetError("subscript expressions are unsupported")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            base = _dependency_offsets(node.func.value, variables)
            operation = node.func.attr.casefold()
            period_position = 0
        elif isinstance(node.func, ast.Name):
            operation = node.func.id.casefold()
            base = _dependency_offsets(node.args[0], variables) if node.args else set()
            period_position = 1
        else:
            raise FormulaOffsetError("anonymous formula calls are unsupported")
        if operation in _UNBOUNDED_TEMPORAL_NAMES_CASEFOLD:
            raise FormulaOffsetError(
                f"unbounded temporal operation is unsupported: {operation}"
            )
        _validate_qualified_call(node, operation)
        if operation not in _KNOWN_FUNCTION_NAMES_CASEFOLD:
            # Returning offsets for an unknown call would treat its control
            # parameters as data and can make a formula appear auditable when
            # its temporal semantics are actually unknown.  Fail closed so a
            # caller can report a data precondition instead of guessing.
            raise FormulaOffsetError(
                f"unknown formula function or method: {operation or '<anonymous>'}"
            )
        _validate_call_keywords(node, operation, variables)
        if operation in {"diff", "pct_change"}:
            if len(node.args) > period_position + 1:
                raise FormulaOffsetError(f"{operation} has too many positional arguments")
            period = _call_period(node, period_position, ("periods",), 1, variables)
            return base | {offset + period for offset in base}
        if operation == "shift":
            if len(node.args) > period_position + 1:
                raise FormulaOffsetError("shift has too many positional arguments")
            period = _call_period(node, period_position, ("periods",), 1, variables)
            return {offset + period for offset in base}
        if operation == "rolling":
            if not base:
                raise FormulaOffsetError("temporal call has no base series")
            if len(node.args) > period_position + 1:
                raise FormulaOffsetError("rolling has too many positional arguments")
            period = _call_period(
                node,
                period_position,
                ("window", "periods", "period"),
                None,
                variables,
            )
            _validate_trailing_rolling_window(node, period, variables)
            return _offset_window(base, period, operation="rolling")
        if (
            isinstance(node.func, ast.Attribute)
            and operation == "corr"
            and _is_finite_rolling_corr_method(node)
        ):
            return _rolling_corr_method_offsets(node, base, variables)
        if (
            isinstance(node.func, ast.Name)
            and operation in {"mean", "std", "sum", "min", "max"}
        ):
            if not node.args:
                raise FormulaOffsetError("temporal call has no base series")
            if len(node.args) > 2:
                raise FormulaOffsetError(f"{operation} has too many positional arguments")
            period = _call_period(
                node,
                period_position,
                ("window", "periods", "period"),
                None,
                variables,
            )
            return _offset_window(base, period, operation=operation)
        if isinstance(node.func, ast.Name) and operation in {
            "atr",
            "corr",
            "correlation",
            "delay",
            "dpo",
            "highest",
            "lowest",
            "ma",
            "percentile_rank",
            "rolling_sum",
            "rolling_vwap",
            "rolling_zscore",
            "rsi",
            "vwap",
            "zscore",
        }:
            return _windowed_function_offsets(node, operation, variables)
        argument_offsets = (
            set().union(*(_dependency_offsets(arg, variables) for arg in node.args))
            if node.args
            else set()
        )
        # Keyword values are data only when they are not static/control
        # parameters.  Temporal parameters are validated above for the
        # operators that consume them; other known control names are ignored
        # rather than being mistaken for raw series.
        for keyword in node.keywords:
            if keyword.arg is not None and keyword.arg.casefold() in _CONTROL_KEYWORDS:
                continue
            argument_offsets |= _dependency_offsets(keyword.value, variables)
        return base | argument_offsets
    raise FormulaOffsetError(f"unsupported formula AST node: {type(node).__name__}")


def _highest_status(statuses: Sequence[CalculationStatus] | Any) -> CalculationStatus:
    return max(tuple(statuses), key=_STATUS_PRIORITY.__getitem__, default="PASS")


def _result(
    case_id: str,
    title: str,
    checked_count: int,
    issues: Sequence[CalculationIssue],
    **evidence: Any,
) -> CalculationCheckResult:
    status = _highest_status(issue.status for issue in issues)
    failed_count = sum(issue.status == "FAIL" for issue in issues)
    data_blocked_count = sum(issue.status == "BLOCKED_DATA_PRECONDITION" for issue in issues)
    doc_blocked_count = sum(issue.status == "BLOCKED_DOC" for issue in issues)
    summary = (
        f"checked={checked_count}, failed={failed_count}, "
        f"blocked_data={data_blocked_count}, blocked_doc={doc_blocked_count}"
    )
    return CalculationCheckResult(
        case_id=case_id,
        title=title,
        status=status,
        summary=summary,
        checked_count=checked_count,
        findings=tuple(issues),
        evidence=dict(evidence),
    )


def _with_additional_findings(
    result: CalculationCheckResult,
    additional: Sequence[CalculationIssue],
) -> CalculationCheckResult:
    """Return ``result`` with deduplicated structured findings appended."""

    if not additional:
        return result
    findings = list(result.findings)
    for finding in additional:
        if finding not in findings:
            findings.append(finding)
    if len(findings) == len(result.findings):
        return result
    return _result(
        result.case_id,
        result.title,
        result.checked_count,
        findings,
        **result.evidence,
    )


def _aggregate_metric_formula_issues(
    issues: Sequence[CalculationIssue],
) -> list[CalculationIssue]:
    grouped: dict[str, list[CalculationIssue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.code].append(issue)

    aggregated: list[CalculationIssue] = []
    for code, same_code in grouped.items():
        status = _highest_status(issue.status for issue in same_code)
        representative = next(issue for issue in same_code if issue.status == status)
        factor_refs = sorted(
            {issue.factor_ref for issue in same_code if issue.factor_ref is not None}
        )
        metric_ids = sorted(
            {
                metric_id
                for issue in same_code
                if (metric_id := issue.evidence.get("metric_id")) is not None
            },
            key=str,
        )
        evidence: dict[str, Any] = {
            "count": len(same_code),
            "factor_ref_count": len(factor_refs),
            "factor_ref_samples": factor_refs[:_ISSUE_SAMPLE_LIMIT],
            "metric_id_samples": metric_ids[:_ISSUE_SAMPLE_LIMIT],
        }
        if code == "METRIC_FORMULA_LINK_INVALID":
            evidence["invalid_field_samples"] = sorted(
                {
                    str(field)
                    for issue in same_code
                    for field in issue.evidence.get("invalid_fields", ())
                }
            )[:_ISSUE_SAMPLE_LIMIT]
            evidence["invalid_reason_samples"] = sorted(
                {
                    str(reason)
                    for issue in same_code
                    for reason in issue.evidence.get("invalid_reasons", ())
                }
            )[:_ISSUE_SAMPLE_LIMIT]
        aggregated.append(
            CalculationIssue(
                status=status,
                code=code,
                message=representative.message,
                evidence=evidence,
            )
        )
    return aggregated


def _blocked(
    code: str,
    message: str,
    factor_ref: str | None = None,
    **evidence: Any,
) -> CalculationIssue:
    return CalculationIssue("BLOCKED_DATA_PRECONDITION", code, message, factor_ref, evidence)


def _blocked_doc(
    code: str,
    message: str,
    factor_ref: str | None = None,
    **evidence: Any,
) -> CalculationIssue:
    """Create a documentation-contract observation without asserting a bug."""

    return CalculationIssue("BLOCKED_DOC", code, message, factor_ref, evidence)


def _failed(
    code: str,
    message: str,
    factor_ref: str | None = None,
    **evidence: Any,
) -> CalculationIssue:
    return CalculationIssue("FAIL", code, message, factor_ref, evidence)


_FACTOR_SNAPSHOT_VERSION_CONFLICT_REASON = (
    "factor_set_snapshot contains conflicting factor versions"
)


def _factor_snapshot_versions(
    snapshot: Mapping[str, Any] | None,
) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    """Extract factor versions and every independently provable snapshot error.

    The full member list is scanned so a malformed member cannot hide a
    conflicting version pair that appears later in the JSON array.  Any error
    makes the returned version map unusable, while callers can still classify
    malformed data and an explicit version contradiction separately.
    """

    if not isinstance(snapshot, Mapping):
        return None, ("factor_set_snapshot is not an object",)
    members = snapshot.get("members")
    if not isinstance(members, Sequence) or isinstance(members, (str, bytes)):
        return None, ("factor_set_snapshot.members is not an array",)
    versions: dict[str, str] = {}
    errors: list[str] = []
    for member in members:
        if not isinstance(member, Mapping):
            errors.append("factor_set_snapshot.members contains a non-object")
            continue
        factor_ref = member.get("factor_ref")
        factor_version = member.get("factor_version")
        if not isinstance(factor_ref, str) or not factor_ref.strip():
            errors.append("factor_set_snapshot member has no factor_ref")
            continue
        if not isinstance(factor_version, str) or not factor_version.strip():
            errors.append("factor_set_snapshot member has no factor_version")
            continue
        normalized_ref = factor_ref.strip()
        normalized_version = factor_version.strip()
        previous = versions.get(normalized_ref)
        if previous is not None and previous != normalized_version:
            errors.append(_FACTOR_SNAPSHOT_VERSION_CONFLICT_REASON)
            continue
        versions.setdefault(normalized_ref, normalized_version)
    unique_errors = tuple(dict.fromkeys(errors))
    return (None if unique_errors else versions), unique_errors


def _reconcile_environment_daily_records(
    batch: PublishedEvaluationBatch,
    environment_members: Sequence[Mapping[str, Any]],
    member_ids: Sequence[int],
    records: Sequence[EnvironmentDailyRecord],
    issues: list[CalculationIssue],
    *,
    history: Sequence[EnvironmentDailyRecord] = (),
    history_loaded: bool = False,
) -> tuple[int, int]:
    """Reconcile frozen daily member identities with DB-backed records.

    ``environment_members`` is the input declaration, while ``records`` are
    the narrow rows read from ``market_environment_daily``.  The return value
    is ``(record_count, missing_member_count)``.  Missing records are an
    explicit reference failure; an entirely absent DB projection is a data
    precondition block.  Optional/null member attributes remain undeclared.
    When ``history_loaded`` is true, explicitly versioned members are also
    checked against all revisions supplied in ``history`` using the batch
    point-in-time boundary.  Invalid record shapes are reported as blocked
    data rather than allowed to raise attribute errors during an audit.
    """

    records_by_id: dict[int, EnvironmentDailyRecord] = {}
    invalid_record_ids: set[int] = set()
    duplicate_ids: set[int] = set()
    for record in records:
        record_id = getattr(record, "id", None)
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_ID_INVALID",
                    "数据库 daily 证据包含无效主键，不能核对冻结成员。",
                )
            )
            continue
        if not isinstance(record, EnvironmentDailyRecord):
            invalid_record_ids.add(record_id)
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_RECORD_INVALID",
                    "数据库 daily 证据不是可核对的 EnvironmentDailyRecord。",
                    daily_id=record_id,
                )
            )
            continue
        shape_valid = True
        if isinstance(record.environment_date, datetime) or not isinstance(
            record.environment_date, date
        ):
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_DATE_INVALID",
                    "数据库 daily 证据 environment_date 不是日历日期。",
                    daily_id=record_id,
                )
            )
        if not isinstance(record.label_kind, str) or not record.label_kind.strip():
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_LABEL_KIND_INVALID",
                    "数据库 daily 证据 label_kind 不是非空字符串。",
                    daily_id=record_id,
                )
            )
        if record.label_code is not None and (
            not isinstance(record.label_code, str) or not record.label_code.strip()
        ):
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_LABEL_CODE_INVALID",
                    "数据库 daily 证据 label_code 不是字符串或 null。",
                    daily_id=record_id,
                )
            )
        if (
            isinstance(record.revision, bool)
            or not isinstance(record.revision, int)
            or record.revision < 1
        ):
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_REVISION_INVALID",
                    "数据库 daily 证据 revision 不是正整数。",
                    daily_id=record_id,
                )
            )
        if not isinstance(record.is_current, bool):
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_CURRENT_INVALID",
                    "数据库 daily 证据 is_current 不是布尔值。",
                    daily_id=record_id,
                )
            )
        if not isinstance(record.available_at, datetime):
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_AVAILABLE_AT_INVALID",
                    "数据库 daily 证据 available_at 不是时间戳。",
                    daily_id=record_id,
                )
            )
        if not isinstance(record.schema_version, str) or not record.schema_version.strip():
            shape_valid = False
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_SCHEMA_VERSION_INVALID",
                    "数据库 daily 证据 schema_version 不是非空字符串。",
                    daily_id=record_id,
                )
            )
        if not shape_valid:
            invalid_record_ids.add(record_id)
            continue
        if record_id in records_by_id:
            duplicate_ids.add(record_id)
        else:
            records_by_id[record_id] = record
    if duplicate_ids:
        issues.append(
            _failed(
                "ROUTE_ENVIRONMENT_DAILY_ID_DUPLICATE",
                "数据库 daily 证据包含重复主键，不能唯一回放冻结成员。",
                count=len(duplicate_ids),
                daily_id_samples=sorted(duplicate_ids)[:_ISSUE_SAMPLE_LIMIT],
            )
        )

    valid_member_ids = set(member_ids)
    if valid_member_ids and not records_by_id:
        issues.append(
            _blocked(
                "ROUTE_ENVIRONMENT_DAILY_EVIDENCE_MISSING",
                "environment_snapshot 有 daily_id，但 Repository 未返回任何对应 daily 证据。",
                expected_daily_count=len(valid_member_ids),
            )
        )
        missing_ids: list[int] = []
    else:
        missing_ids = sorted(
            valid_member_ids - set(records_by_id) - invalid_record_ids
        )
    if missing_ids:
        issues.append(
            _failed(
                "ROUTE_ENVIRONMENT_DAILY_ROW_MISSING",
                "environment_snapshot 引用的 daily 行在数据库中不存在。",
                count=len(missing_ids),
                daily_id_samples=missing_ids[:_ISSUE_SAMPLE_LIMIT],
            )
        )

    members_by_id: dict[int, Mapping[str, Any]] = {}
    for member in environment_members:
        daily_id = member.get("daily_id")
        if isinstance(daily_id, int) and not isinstance(daily_id, bool) and daily_id > 0:
            members_by_id.setdefault(daily_id, member)

    member_available_timezone_ids: list[int] = []
    batch_as_of_timezone_ids: list[int] = []

    for daily_id, record in records_by_id.items():
        member = members_by_id.get(daily_id)
        if member is None:
            continue

        member_date_raw = member.get("environment_date")
        member_date = _snapshot_date(member_date_raw) if member_date_raw is not None else None
        if member_date_raw is not None and member_date is None:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_MEMBER_DATE_INVALID",
                    "environment_snapshot 成员 environment_date 不是有效日期。",
                    daily_id=daily_id,
                )
            )
        elif member_date is not None and record.environment_date != member_date:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DAILY_DATE_MISMATCH",
                    "冻结成员 environment_date 与数据库 daily 行不一致。",
                    daily_id=daily_id,
                    expected_environment_date=member_date.isoformat(),
                    actual_environment_date=record.environment_date.isoformat(),
                )
            )

        member_label_kind = member.get("label_kind")
        if member_label_kind is not None:
            if not isinstance(member_label_kind, str) or not member_label_kind.strip():
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_LABEL_KIND_INVALID",
                        "environment_snapshot 成员 label_kind 不是有效字符串。",
                        daily_id=daily_id,
                    )
                )
            elif record.label_kind != member_label_kind.strip():
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_LABEL_KIND_MISMATCH",
                        "冻结成员 label_kind 与数据库 daily 行不一致。",
                        daily_id=daily_id,
                        expected_label_kind=member_label_kind.strip(),
                        actual_label_kind=record.label_kind,
                    )
                )
        elif record.label_kind != batch.label_kind:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DAILY_BATCH_LABEL_KIND_MISMATCH",
                    "数据库 daily 行的 label_kind 与评估 batch 不一致。",
                    daily_id=daily_id,
                    expected_label_kind=batch.label_kind,
                    actual_label_kind=record.label_kind,
                )
            )

        member_label_code = member.get("label_code")
        if member_label_code is not None:
            if not isinstance(member_label_code, str) or not member_label_code.strip():
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_LABEL_CODE_INVALID",
                        "environment_snapshot 成员 label_code 不是有效字符串。",
                        daily_id=daily_id,
                    )
                )
            elif record.label_code != member_label_code.strip():
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_LABEL_CODE_MISMATCH",
                        "冻结成员 label_code 与数据库 daily 行不一致。",
                        daily_id=daily_id,
                        expected_label_code=member_label_code.strip(),
                        actual_label_code=record.label_code,
                    )
                )
        elif record.label_code is None:
            # A NULL label_code is valid for a not_ready/invalid daily row,
            # but it cannot establish the label identity required by an
            # active route.  Keep this as a data precondition until the
            # producer explicitly declares such rows routable.
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_LABEL_CODE_MISSING",
                    "数据库 daily 行 label_code 为空，不能确认其与 route 的 label 身份一致。",
                    daily_id=daily_id,
                )
            )

        member_revision = member.get("revision")
        if member_revision is not None:
            if (
                isinstance(member_revision, bool)
                or not isinstance(member_revision, int)
                or member_revision < 1
            ):
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_REVISION_INVALID",
                        "environment_snapshot 成员 revision 不是有效正整数。",
                        daily_id=daily_id,
                    )
                )
            elif record.revision != member_revision:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_REVISION_MISMATCH",
                        "冻结成员 revision 与数据库 daily 行不一致。",
                        daily_id=daily_id,
                        expected_revision=member_revision,
                        actual_revision=record.revision,
                    )
                )

        member_schema_version = member.get("schema_version")
        if member_schema_version is not None:
            if not isinstance(member_schema_version, str) or not member_schema_version.strip():
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_SCHEMA_VERSION_INVALID",
                        "environment_snapshot 成员 schema_version 不是有效字符串。",
                        daily_id=daily_id,
                    )
                )
            elif record.schema_version != member_schema_version.strip():
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_SCHEMA_VERSION_MISMATCH",
                        "冻结成员 schema_version 与数据库 daily 行不一致。",
                        daily_id=daily_id,
                        expected_schema_version=member_schema_version.strip(),
                        actual_schema_version=record.schema_version,
                    )
                )

        member_is_current = member.get("is_current")
        if member_is_current is not None:
            normalized_current: bool | None = None
            if isinstance(member_is_current, bool):
                normalized_current = member_is_current
            elif isinstance(member_is_current, int) and member_is_current in (0, 1):
                normalized_current = bool(member_is_current)
            if normalized_current is None:
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_CURRENT_INVALID",
                        "environment_snapshot 成员 is_current 不是有效布尔值。",
                        daily_id=daily_id,
                    )
                )
            elif bool(record.is_current) != normalized_current:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_CURRENT_MISMATCH",
                        "冻结成员 is_current 与数据库 daily 行不一致。",
                        daily_id=daily_id,
                        expected_is_current=normalized_current,
                        actual_is_current=bool(record.is_current),
                    )
                )

        member_available_at_raw = member.get("available_at")
        if member_available_at_raw is not None:
            member_available_at = _snapshot_datetime(member_available_at_raw)
            if member_available_at is None:
                issues.append(
                    _blocked(
                        "ROUTE_ENVIRONMENT_MEMBER_AVAILABLE_AT_INVALID",
                        "environment_snapshot 成员 available_at 不可解析。",
                        daily_id=daily_id,
                    )
                )
            else:
                available_match = _same_timestamp_instant(
                    member_available_at,
                    record.available_at,
                )
                if available_match is None:
                    member_available_timezone_ids.append(daily_id)
                elif not available_match:
                    issues.append(
                        _failed(
                            "ROUTE_ENVIRONMENT_DAILY_AVAILABLE_AT_MISMATCH",
                            "冻结成员 available_at 与数据库 daily 行不一致。",
                            daily_id=daily_id,
                            expected_available_at=member_available_at.isoformat(),
                            actual_available_at=record.available_at.isoformat(),
                        )
                    )

        available_at = getattr(record, "available_at", None)
        batch_as_of = getattr(batch, "as_of_time", None)
        if not isinstance(available_at, datetime) or not isinstance(batch_as_of, datetime):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_TIMESTAMP_INVALID",
                    "daily.available_at 或 batch.as_of_time 不是有效时间戳，不能确认可见性。",
                    daily_id=daily_id,
                )
            )
        else:
            if available_at.tzinfo is None and batch_as_of.tzinfo is None:
                is_future = available_at > batch_as_of
                comparable = True
            elif available_at.tzinfo is not None and batch_as_of.tzinfo is not None:
                is_future = available_at.astimezone(timezone.utc) > batch_as_of.astimezone(timezone.utc)
                comparable = True
            else:
                is_future = False
                comparable = False
            if not comparable:
                batch_as_of_timezone_ids.append(daily_id)
            elif is_future:
                issues.append(
                    _failed(
                        "ROUTE_ENVIRONMENT_DAILY_NOT_VISIBLE_AT_BATCH_AS_OF",
                        "冻结 batch 的 as_of_time 早于 daily.available_at，route 不能使用未来才可见的环境数据。",
                        daily_id=daily_id,
                        batch_as_of_time=batch_as_of.isoformat(),
                        available_at=available_at.isoformat(),
                    )
                )

    if member_available_timezone_ids:
        issues.append(
            _blocked_doc(
                "ROUTE_ENVIRONMENT_DAILY_AVAILABLE_AT_TIMEZONE_UNDEFINED",
                "冻结成员与数据库 daily.available_at 的时区表示不同，当前契约未声明 naive DB 时间时区。",
                count=len(member_available_timezone_ids),
                daily_id_samples=member_available_timezone_ids[:_ISSUE_SAMPLE_LIMIT],
            )
        )
    if batch_as_of_timezone_ids:
        issues.append(
            _blocked_doc(
                "ROUTE_ENVIRONMENT_DAILY_AS_OF_TIMEZONE_UNDEFINED",
                "数据库 daily.available_at 与 batch.as_of_time 的时区表示不同，当前契约未声明 naive DB 时间时区。",
                count=len(batch_as_of_timezone_ids),
                daily_id_samples=batch_as_of_timezone_ids[:_ISSUE_SAMPLE_LIMIT],
            )
        )

    _reconcile_environment_daily_history(
        batch,
        environment_members,
        records_by_id,
        history,
        history_loaded,
        issues,
    )

    return len(records_by_id), len(missing_ids)


def _reconcile_environment_daily_history(
    batch: PublishedEvaluationBatch,
    environment_members: Sequence[Mapping[str, Any]],
    selected_records: Mapping[int, EnvironmentDailyRecord],
    history: Sequence[EnvironmentDailyRecord],
    history_loaded: bool,
    issues: list[CalculationIssue],
) -> None:
    """Validate revision/PIT selection for members that declare a revision.

    A member without an explicit revision is intentionally left in the legacy
    identity-only path.  For an explicitly versioned member, the repository
    must provide the complete revision set for its date/kind; the selected row
    must be the highest revision whose ``available_at`` is not after the batch
    ``as_of_time``.  Missing history is a data precondition, while selecting a
    lower/otherwise invisible revision is a concrete product failure.
    """

    versioned: list[tuple[int, Mapping[str, Any], EnvironmentDailyRecord]] = []
    for member in environment_members:
        daily_id = member.get("daily_id")
        revision = member.get("revision")
        if (
            isinstance(daily_id, bool)
            or not isinstance(daily_id, int)
            or daily_id < 1
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            continue
        selected = selected_records.get(daily_id)
        if selected is not None:
            versioned.append((daily_id, member, selected))
    if not versioned:
        return

    if not history_loaded:
        issues.append(
            _blocked(
                "ROUTE_ENVIRONMENT_DAILY_HISTORY_MISSING",
                "冻结成员显式声明 revision，但未加载同日期/label_kind 的完整历史 revision 证据。",
                count=len(versioned),
                daily_id_samples=[item[0] for item in versioned[:_ISSUE_SAMPLE_LIMIT]],
            )
        )
        return

    history_by_key: dict[tuple[date, str], list[EnvironmentDailyRecord]] = defaultdict(list)
    history_seen_ids: set[int] = set()
    history_duplicate_keys: set[tuple[date, str, int]] = set()
    for record in history:
        if not isinstance(record, EnvironmentDailyRecord):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_RECORD_INVALID",
                    "历史 daily revision 证据包含不可核对的记录类型。",
                )
            )
            continue
        if (
            isinstance(record.id, bool)
            or not isinstance(record.id, int)
            or record.id < 1
            or isinstance(record.environment_date, datetime)
            or not isinstance(record.environment_date, date)
            or not isinstance(record.label_kind, str)
            or not record.label_kind.strip()
            or isinstance(record.revision, bool)
            or not isinstance(record.revision, int)
            or record.revision < 1
            or not isinstance(record.available_at, datetime)
        ):
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_RECORD_INVALID",
                    "历史 daily revision 证据的身份或时间字段无效。",
                    daily_id=record.id if isinstance(record.id, int) and not isinstance(record.id, bool) else None,
                )
            )
            continue
        if record.id in history_seen_ids:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_ID_DUPLICATE",
                    "历史 daily revision 证据包含重复主键。",
                    daily_id=record.id,
                )
            )
            continue
        history_seen_ids.add(record.id)
        key = (record.environment_date, record.label_kind.strip())
        revision_key = (*key, record.revision)
        if any(existing.revision == record.revision for existing in history_by_key[key]):
            history_duplicate_keys.add(revision_key)
        history_by_key[key].append(record)
    if history_duplicate_keys:
        issues.append(
            _failed(
                "ROUTE_ENVIRONMENT_DAILY_HISTORY_REVISION_DUPLICATE",
                "同一 environment_date/label_kind 存在多个相同 revision，无法唯一回放 PIT 版本。",
                count=len(history_duplicate_keys),
                key_samples=[
                    {
                        "environment_date": key[0].isoformat(),
                        "label_kind": key[1],
                        "revision": key[2],
                    }
                    for key in sorted(history_duplicate_keys)[:_ISSUE_SAMPLE_LIMIT]
                ],
            )
        )

    batch_as_of = batch.as_of_time
    if not isinstance(batch_as_of, datetime):
        issues.append(
            _blocked(
                "ROUTE_ENVIRONMENT_DAILY_HISTORY_AS_OF_INVALID",
                "batch.as_of_time 不是有效时间戳，不能执行历史 revision 可见性判断。",
            )
        )
        return

    for daily_id, member, selected in versioned:
        key = (selected.environment_date, selected.label_kind.strip())
        candidates = history_by_key.get(key, [])
        if not candidates:
            issues.append(
                _blocked(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_KEY_MISSING",
                    "未找到冻结成员对应日期/label_kind 的历史 revision 集合。",
                    daily_id=daily_id,
                    environment_date=selected.environment_date.isoformat(),
                    label_kind=selected.label_kind,
                )
            )
            continue
        visible_revisions: list[int] = []
        timezone_undefined = False
        for candidate in candidates:
            comparable = _timestamp_is_at_or_before(candidate.available_at, batch_as_of)
            if comparable is None:
                timezone_undefined = True
            elif comparable:
                visible_revisions.append(candidate.revision)
        if timezone_undefined:
            issues.append(
                _blocked_doc(
                    "ROUTE_ENVIRONMENT_DAILY_HISTORY_AS_OF_TIMEZONE_UNDEFINED",
                    "历史 daily.available_at 与 batch.as_of_time 的时区表示不同，当前契约未声明 naive 时间的时区。",
                    daily_id=daily_id,
                )
            )
            continue
        if not visible_revisions:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DAILY_PIT_REVISION_NOT_VISIBLE",
                    "冻结成员 revision 在 batch.as_of_time 时点尚不可见。",
                    daily_id=daily_id,
                    expected_revision=member.get("revision"),
                    batch_as_of_time=batch_as_of.isoformat(),
                )
            )
            continue
        expected_revision = max(visible_revisions)
        actual_revision = member.get("revision")
        if actual_revision != expected_revision:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DAILY_PIT_REVISION_MISMATCH",
                    "冻结成员未选择 batch.as_of_time 前可见的最高 revision。",
                    daily_id=daily_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                    environment_date=selected.environment_date.isoformat(),
                    label_kind=selected.label_kind,
                )
            )


def _timestamp_is_at_or_before(left: datetime, right: datetime) -> bool | None:
    """Compare two timestamps when their timezone styles are compatible."""

    if left.tzinfo is None and right.tzinfo is None:
        return left <= right
    if left.tzinfo is not None and right.tzinfo is not None:
        return left.astimezone(timezone.utc) <= right.astimezone(timezone.utc)
    return None


def _snapshot_date(value: Any) -> date | None:
    """Parse a JSON/date value without accepting ambiguous datetime strings."""

    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _snapshot_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp from snapshot JSON while preserving its timezone.

    Date-only strings are deliberately rejected.  ``datetime.fromisoformat``
    accepts ``YYYY-MM-DD`` as midnight, but treating a calendar date as an
    exact publication instant would create a false point-in-time boundary.
    Naive timestamps remain accepted because the current MySQL DATETIME
    projection is naive; callers decide whether a mixed aware/naive comparison
    is permitted by the surrounding contract.
    """

    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _route_environment_date_semantics(
    environment_snapshot: Mapping[str, Any] | None,
) -> Literal["snapshot_member", "publication_effective"] | None:
    """Normalize the explicitly declared meaning of ``route.environment_date``.

    The current catalog sometimes omits this field.  Returning ``None`` in
    that case is intentional: callers must not invent a date equality rule.
    """

    if not isinstance(environment_snapshot, Mapping):
        return None
    raw_semantics = environment_snapshot.get(
        "route_environment_date_semantics",
        environment_snapshot.get("route_date_semantics"),
    )
    if not isinstance(raw_semantics, str):
        return None
    normalized = raw_semantics.strip().casefold()
    if normalized in {"snapshot_member", "snapshot_members", "member"}:
        return "snapshot_member"
    if normalized in {
        "publication_effective",
        "publication_effective_date",
        "effective",
    }:
        return "publication_effective"
    return None


def _same_timestamp_instant(left: datetime, right: datetime) -> bool | None:
    """Compare timestamps, or return ``None`` when a timezone is unknowable.

    MySQL ``DATETIME`` values are often returned without timezone metadata,
    while JSON snapshots may carry ``Z``/``+08:00``.  Treating a naive value
    as UTC would create a deterministic but potentially false defect, so a
    mixed aware/naive pair is reported as a documentation observation.  Two
    values with the same style are compared as exact instants.
    """

    left_aware = left.tzinfo is not None
    right_aware = right.tzinfo is not None
    if left_aware != right_aware:
        return None
    if not left_aware:
        return left == right
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


def _group_by_ref(values: Sequence[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for value in values:
        grouped[value.factor_ref].append(value)
    return grouped


def _membership_difference_issues(
    snapshot: CalculationAuditSnapshot,
) -> list[CalculationIssue]:
    """Turn Repository membership reconciliation into structured findings.

    The Repository compares the frozen ``factor_set_snapshot`` with the factor
    identities present in the batch metrics.  A non-empty difference is an
    explicit batch-integrity contradiction and therefore fails the calculation
    check; a missing or malformed difference object is a data precondition
    block.  Only bounded identity samples are copied into evidence so a large
    publication cannot make diagnostics unbounded.
    """

    differences = getattr(snapshot, "membership_differences", None)
    if not isinstance(differences, FactorMembershipDifferences):
        return [
            _blocked(
                "FACTOR_MEMBERSHIP_DIFFERENCES_MISSING",
                "批次缺少冻结成员与 metric 成员的对账结果，不能确认成员集合一致。",
            )
        ]

    issues: list[CalculationIssue] = []
    directions = (
        (
            "missing_from_metrics",
            "FACTOR_MEMBERSHIP_MISSING_FROM_METRICS",
            "冻结因子成员在 metric 集合中缺失，批次成员与评估证据不一致。",
        ),
        (
            "unexpected_in_metrics",
            "FACTOR_MEMBERSHIP_UNEXPECTED_IN_METRICS",
            "metric 集合包含不在冻结因子成员中的因子，批次成员与评估证据不一致。",
        ),
    )
    unresolved = getattr(differences, "missing_definition_versions", ())
    if not isinstance(unresolved, Sequence) or isinstance(unresolved, (str, bytes)):
        issues.append(
            _blocked(
                "FACTOR_MEMBERSHIP_DIFFERENCES_INVALID",
                "membership_differences.missing_definition_versions 不是有效的版本问题序列。",
            )
        )
    elif unresolved:
        if any(not isinstance(issue, FactorMembershipVersionIssue) for issue in unresolved):
            issues.append(
                _blocked(
                    "FACTOR_MEMBERSHIP_DIFFERENCES_INVALID",
                    "membership_differences.missing_definition_versions 含有无效的问题对象。",
                )
            )
        else:
            factor_refs = sorted({issue.factor_ref for issue in unresolved})
            issues.append(
                _blocked(
                    "FACTOR_MEMBERSHIP_DEFINITION_VERSION_MISSING",
                    "metric 缺少或冲突的 definition_factor_version，无法与冻结成员进行可靠对账。",
                    factor_ref=factor_refs[0] if len(factor_refs) == 1 else None,
                    count=len(unresolved),
                    factor_ref_count=len(factor_refs),
                    factor_ref_samples=factor_refs[:_ISSUE_SAMPLE_LIMIT],
                    issues=[
                        {
                            "factor_ref": issue.factor_ref,
                            "factor_type": issue.factor_type,
                            "factor_id": issue.factor_id,
                            "reason": issue.reason,
                            "executable_factor_version": issue.executable_factor_version,
                        }
                        for issue in unresolved[:_ISSUE_SAMPLE_LIMIT]
                    ],
                )
            )
    for attribute, code, message in directions:
        identities = getattr(differences, attribute, None)
        if not isinstance(identities, Sequence) or isinstance(identities, (str, bytes)):
            issues.append(
                _blocked(
                    "FACTOR_MEMBERSHIP_DIFFERENCES_INVALID",
                    f"membership_differences.{attribute} 不是有效的因子身份序列。",
                )
            )
            continue
        if not identities:
            continue
        if any(not isinstance(identity, FactorIdentity) for identity in identities):
            issues.append(
                _blocked(
                    "FACTOR_MEMBERSHIP_DIFFERENCES_INVALID",
                    f"membership_differences.{attribute} 含有无效的因子身份。",
                )
            )
            continue
        identity_samples = [
            {
                "factor_ref": identity.factor_ref,
                "factor_type": identity.factor_type,
                "factor_id": identity.factor_id,
                "factor_version": identity.factor_version,
            }
            for identity in identities[:_ISSUE_SAMPLE_LIMIT]
        ]
        factor_refs = sorted({identity.factor_ref for identity in identities})
        issues.append(
            _failed(
                code,
                message,
                factor_ref=factor_refs[0] if len(factor_refs) == 1 else None,
                count=len(identities),
                factor_ref_count=len(factor_refs),
                factor_ref_samples=factor_refs[:_ISSUE_SAMPLE_LIMIT],
                identity_samples=identity_samples,
            )
        )
    return issues


def _latest_detail(details: Sequence[FactorDetail]) -> FactorDetail | None:
    if not details:
        return None
    return max(details, key=lambda row: (_datetime_sort_key(row.updated_at), row.id))


def _latest_definition(
    definitions: Sequence[FactorDefinition],
) -> FactorDefinition | None:
    """Select the newest catalog definition without mixing timezone styles."""

    if not definitions:
        return None
    return max(
        definitions,
        key=lambda row: _datetime_sort_key(row.definition_updated_at),
    )


def _datetime_sort_key(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _batch_detail_items(response: MCPResponse) -> list[dict[str, Any]]:
    data = _tool_data(response)
    items = data.get("items") if data is not None else None
    if not isinstance(items, list):
        raise MCPProtocolError("factor_get_details_batch data.items must be an array")
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _tool_data(response: MCPResponse) -> dict[str, Any] | None:
    if response.is_tool_error:
        return None
    structured = response.structured_content
    if not isinstance(structured, Mapping):
        return None
    data = structured.get("data")
    return dict(data) if isinstance(data, Mapping) else None


def _tool_error_code(response: MCPResponse) -> str:
    """Return a credential-free code for a legal MCP tool-level error.

    ``result.isError=true`` is a business response, not a JSON-RPC failure.
    The helper intentionally keeps only a bounded string code so diagnostics
    cannot accidentally copy a server error message or sensitive payload.
    Unknown shapes are represented by a stable generic code; malformed
    envelopes are rejected earlier by the MCP API and never reach this helper.
    """

    structured = response.structured_content
    error = structured.get("error") if isinstance(structured, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    if isinstance(code, (str, int)) and not isinstance(code, bool):
        normalized = str(code).strip()
        if normalized:
            return f"MCP_TOOL_ERROR:{normalized[:120]}"
    return "MCP_TOOL_ERROR"


def _mcp_snapshot_scope(snapshot: CalculationAuditSnapshot) -> tuple[Any, ...]:
    """Build a hashable cache scope from MCP-relevant snapshot evidence.

    Object identity and the capture timestamp separate independent reads.
    Detail, formula, and metric-link fields are included as a defensive guard
    when a snapshot is mutated in a test fixture. JSON fields are
    canonicalized only for the scope token; their business values are never
    modified.
    """

    batch = snapshot.batch
    detail_rows = tuple(
        sorted(
            [
                (
                detail.factor_ref,
                detail.id,
                detail.batch_factor_version,
                detail.updated_at,
                detail.calc_logic,
                _scope_json(detail.params),
                _scope_json(detail.data_source_metadata),
                )
                for detail in snapshot.details
            ],
            key=repr,
        )
    )
    formula_rows = tuple(
        sorted(
            [
                (
                formula.id,
                formula.factor_ref,
                formula.run_id,
                formula.calculation_mode,
                formula.factor_bar_interval,
                formula.factor_window_bars,
                formula.return_bar_interval,
                formula.forward_return_bars,
                formula.formula_version,
                formula.formula_hash,
                formula.source_detail_id,
                formula.formula_factor_version,
                formula.expression,
                _scope_json(formula.required_fields),
                formula.run_status,
                formula.run_completed_at,
                )
                for formula in snapshot.formula_evidence
            ],
            key=repr,
        )
    )
    metric_rows = tuple(
        sorted(
            [
                (
                metric.id,
                metric.factor_ref,
                metric.factor_version,
                metric.interval,
                metric.return_bar_interval,
                metric.forward_return_bars,
                _scope_json(metric.metric_identity),
                )
                for metric in snapshot.evaluation_metrics
            ],
            key=repr,
        )
    )
    return (
        id(snapshot),
        batch.id,
        batch.batch_uid,
        batch.publication_uid,
        batch.publish_version,
        batch.market_scope,
        batch.route_profile_key,
        snapshot.captured_at,
        detail_rows,
        formula_rows,
        metric_rows,
    )


def _scope_json(value: Any) -> str:
    """Return a deterministic, bounded-type representation for cache scope."""

    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        # Malformed fixture values should still invalidate the scope rather
        # than make the diagnostic service crash while constructing a token.
        return repr(value)


def _formula_key(evidence: FormulaEvidence) -> tuple[Any, ...]:
    """Return the complete immutable identity for one formula MCP read.

    Request dimensions alone are insufficient when duplicate evidence rows
    share a run/context but carry different hashes or versions.  Including
    both the database evidence identity and the request dimensions prevents
    one row's cached projection from being silently reused for another.
    """

    return (
        evidence.factor_ref,
        evidence.run_id,
        evidence.calculation_mode,
        evidence.factor_bar_interval,
        evidence.factor_window_bars,
        evidence.return_bar_interval,
        evidence.forward_return_bars,
        evidence.id,
        evidence.formula_version,
        evidence.formula_hash,
        evidence.hash_algorithm,
        evidence.normalization_version,
        evidence.source_detail_id,
        evidence.formula_factor_version,
    )


def _is_completed_formula_evidence(evidence: FormulaEvidence) -> bool:
    """Return whether an evidence row has a completed immutable run."""

    return (
        str(evidence.run_status).casefold() == "completed"
        and evidence.run_completed_at is not None
    )


def _formula_expression_node(expression: Any) -> ast.AST | None:
    """Parse an expression or a single assignment/return wrapper.

    Catalog rows are not consistent about whether ``calc_logic`` contains a
    bare expression or a short executable snippet such as ``factor = ...``.
    Only the value expression is admitted; arbitrary statements are rejected
    so this helper remains a comparison parser rather than an evaluator.
    """

    if not isinstance(expression, str) or not expression.strip():
        return None
    text = expression.strip()
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError:
        pass
    try:
        module = ast.parse(text, mode="exec")
    except SyntaxError:
        return None
    if len(module.body) != 1:
        return None
    statement = module.body[0]
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        return statement.value
    if isinstance(statement, ast.AnnAssign):
        return statement.value
    if isinstance(statement, ast.Return):
        return statement.value
    if isinstance(statement, ast.Expr):
        return statement.value
    return None


class _FormulaExpressionNormalizer(ast.NodeTransformer):
    """Substitute declared variables and fold integer arithmetic in an AST."""

    def __init__(self, variables: Mapping[str, int] | None = None) -> None:
        self._variables = {
            str(name).casefold(): value
            for name, value in (variables or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }

    def visit_Name(self, node: ast.Name) -> ast.AST:
        """Replace a declared integer variable while preserving unknown names."""

        value = self._variables.get(node.id.casefold())
        if value is None:
            return node
        return ast.copy_location(ast.Constant(value=value), node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Fold statically resolvable integer arithmetic after visiting children."""

        normalized = self.generic_visit(node)
        if not isinstance(normalized, ast.BinOp):
            return normalized
        try:
            value = _static_int(normalized)
        except (FormulaOffsetError, TypeError, ValueError):
            return normalized
        return ast.copy_location(ast.Constant(value=value), normalized)


def _canonical_expression(
    expression: Any,
    *,
    variables: Mapping[str, int] | None = None,
) -> str | None:
    """Return a stable AST form, accepting parameterized executable snippets.

    Canonicalization is only a comparison aid; it must not turn an expression
    with unknown call/parameter semantics into proof of equivalence.  Run the
    same fail-closed offset validator first and return ``None`` when the
    expression cannot be statically justified.
    """

    node = _formula_expression_node(expression)
    if node is None:
        return None
    try:
        _dependency_offsets(node, variables)
    except FormulaOffsetError:
        return None
    normalized = _FormulaExpressionNormalizer(variables).visit(node)
    return ast.dump(normalized, include_attributes=False)


def _expressions_equivalent(
    first: Any,
    second: Any,
    *,
    variables: Mapping[str, int] | None = None,
) -> bool:
    first_canonical = _canonical_expression(first, variables=variables)
    second_canonical = _canonical_expression(second, variables=variables)
    return first_canonical is not None and first_canonical == second_canonical


def _formula_candidate(
    label: str,
    value: Any,
    *,
    variables: Mapping[str, int] | None = None,
) -> tuple[str, str] | None:
    """Return one statically auditable formula candidate, ignoring prose.

    A syntactically valid but semantically unknown call is not a candidate:
    treating it as an executable formula would allow identical unknown strings
    in DB/MCP projections to produce a false PASS.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    node = _formula_expression_node(value)
    if node is None:
        return None
    try:
        _dependency_offsets(node, variables)
    except FormulaOffsetError:
        return None
    return label, value.strip()


def _definition_formula_candidates(
    definition: FactorDefinition,
    *,
    variables: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    candidate = _formula_candidate(
        "current.db_definition.formula_summary",
        definition.formula_summary,
        variables=variables,
    )
    return [candidate] if candidate is not None else []


def _detail_formula_candidates(
    detail: FactorDetail | None,
    *,
    variables: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    if detail is None:
        return []
    candidates: list[tuple[str, str]] = []
    values: list[tuple[str, Any]] = [("current.db_detail.calc_logic", detail.calc_logic)]
    for container_name, container in (("params", detail.params),):
        if isinstance(container, Mapping):
            for key in ("calc_logic", "formula", "expression"):
                if key in container:
                    values.append((f"current.db_detail.{container_name}.{key}", container[key]))
    for label, value in values:
        candidate = _formula_candidate(label, value, variables=variables)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _mcp_formula_candidates(
    detail: Mapping[str, Any] | None,
    *,
    variables: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    if not isinstance(detail, Mapping):
        return []
    candidates: list[tuple[str, str]] = []
    values: list[tuple[str, Any]] = []
    # Summaries and normalized provenance are audited separately. A stale
    # metadata expression is not proof that a completed run executed it.
    for key in ("calc_logic", "formula", "expression"):
        if key in detail:
            values.append((f"current.mcp_detail.{key}", detail[key]))
    for container_name in ("params",):
        container = detail.get(container_name)
        if isinstance(container, Mapping):
            for key in ("calc_logic", "formula", "expression"):
                if key in container:
                    values.append((f"current.mcp_detail.{container_name}.{key}", container[key]))
    for label, value in values:
        candidate = _formula_candidate(label, value, variables=variables)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


class _FormulaPeriodMask(ast.NodeTransformer):
    """Mask explicit temporal periods only to detect unresolved unit changes."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        """Return the call with supported period arguments masked, not evaluated."""

        normalized = self.generic_visit(node)
        if not isinstance(normalized, ast.Call):
            return normalized
        position: int | None = None
        if isinstance(normalized.func, ast.Name) and normalized.func.id.casefold() in {
            "mean", "std", "sum", "min", "max", "diff", "pct_change", "shift", "rolling",
        }:
            position = 1
        elif isinstance(normalized.func, ast.Attribute) and normalized.func.attr.casefold() in {
            "rolling", "diff", "pct_change", "shift",
        }:
            position = 0
        if position is None:
            return normalized
        if len(normalized.args) > position:
            normalized.args[position] = ast.Constant(value="temporal-period")
        for keyword in normalized.keywords:
            if keyword.arg and keyword.arg.casefold() in {"window", "period", "periods"}:
                keyword.value = ast.Constant(value="temporal-period")
        return normalized


def _formula_period_shape(expression: str, variables: Mapping[str, int] | None) -> str | None:
    """Return a period-insensitive shape, never a proof of numerical equivalence."""

    node = _formula_expression_node(expression)
    if node is None or _canonical_expression(expression, variables=variables) is None:
        return None
    normalized = _FormulaExpressionNormalizer(variables).visit(node)
    return ast.dump(_FormulaPeriodMask().visit(normalized), include_attributes=False)


def _compare_normalized_formula_metadata(
    factor_ref: str,
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any] | None,
    issues: list[CalculationIssue],
    *,
    variables: Mapping[str, int] | None = None,
) -> None:
    """Audit normalized metadata without attributing it to executable Run output."""

    sources: list[tuple[str, Mapping[str, Any], str | None]] = []
    if detail is not None:
        for name, value in (("params", detail.params), ("data_source_metadata", detail.data_source_metadata)):
            if isinstance(value, Mapping):
                sources.append((f"current.db_detail.{name}", value, detail.calc_logic))
    if isinstance(mcp_detail, Mapping):
        executable = mcp_detail.get("calc_logic")
        if not isinstance(executable, str) and detail is not None:
            executable = detail.calc_logic
        sources.append(("current.mcp_detail", mcp_detail, executable))
        for name in ("params", "metadata", "data_source_metadata"):
            value = mcp_detail.get(name)
            if isinstance(value, Mapping):
                sources.append((f"current.mcp_detail.{name}", value, executable))
    for source, value, executable in sources:
        normalized = value.get("normalized_formula")
        if not isinstance(normalized, str) or not normalized.strip():
            continue
        if _expressions_equivalent(executable, normalized, variables=variables):
            continue
        window = (variables or {}).get("window")
        if isinstance(executable, str) and window is not None and (
            _is_correct_dpo(executable, window) and _is_correct_dpo(normalized, window)
        ):
            continue
        execution_shape = _formula_period_shape(executable, variables) if isinstance(executable, str) else None
        metadata_shape = _formula_period_shape(normalized, variables)
        if execution_shape is None or metadata_shape is None:
            issues.append(_blocked(
                "NORMALIZED_FORMULA_COMPARISON_UNRESOLVED",
                "归一化元数据或当前执行表达式不可静态解析，不能证明两者语义一致。",
                factor_ref,
                source=f"{source}.normalized_formula",
            ))
        elif execution_shape == metadata_shape:
            # Period-only changes may be legitimate hours-to-bars normalization.
            # Catalog cadence is not proof of the actual runtime input cadence.
            issues.append(_blocked_doc(
                "NORMALIZED_FORMULA_TEMPORAL_BASIS_UNRESOLVED",
                "归一化元数据与执行表达式仅周期参数不同，缺少实际输入周期及单位转换证据。",
                factor_ref,
                source=f"{source}.normalized_formula",
            ))
        elif isinstance(executable, str) and window is not None and _is_correct_dpo(executable, window):
            issues.append(_failed(
                "F4-NORMALIZED-FORMULA-STALE",
                "公式已更新但 normalized_formula 元数据仍保留旧表达式",
                factor_ref,
                source=f"{source}.normalized_formula",
                evidence_layer="current_metadata_not_run_execution",
            ))
        else:
            issues.append(_blocked_doc(
                "NORMALIZED_FORMULA_SEMANTICS_UNRESOLVED",
                "归一化元数据与当前执行表达式不同，缺少可证明归一化规则的证据。",
                factor_ref,
                source=f"{source}.normalized_formula",
            ))


def _candidate_conflicts(
    candidates: Sequence[tuple[str, str]],
    *,
    variables: Mapping[str, int] | None = None,
) -> bool:
    canonical = {
        value
        for _, expression in candidates
        if (value := _canonical_expression(expression, variables=variables)) is not None
    }
    return len(canonical) > 1


def _candidate_sets_equivalent(
    first: Sequence[tuple[str, str]],
    second: Sequence[tuple[str, str]],
    *,
    variables: Mapping[str, int] | None = None,
) -> bool:
    first_canonical = {
        value
        for _, expression in first
        if (value := _canonical_expression(expression, variables=variables)) is not None
    }
    second_canonical = {
        value
        for _, expression in second
        if (value := _canonical_expression(expression, variables=variables)) is not None
    }
    return bool(first_canonical and second_canonical and first_canonical == second_canonical)


def _compare_definition_projection(
    definition: FactorDefinition,
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any],
    issues: list[CalculationIssue],
    *,
    include_internal_semantics: bool = True,
) -> None:
    comparisons = {
        "name": (definition.name, mcp_detail.get("name")),
        "serial_number": (definition.serial_number, mcp_detail.get("serial_number")),
        "window": (definition.window, mcp_detail.get("window")),
        "factor_bar_interval": (definition.factor_bar_interval, mcp_detail.get("factor_bar_interval")),
    }
    for field_name, (stored, returned) in comparisons.items():
        if stored is not None and returned is not None and str(stored) != str(returned):
            issues.append(
                _failed(
                    "MCP_DETAIL_PROJECTION_MISMATCH",
                    "MCP 详情与数据库当前定义字段不一致。",
                    definition.factor_ref,
                    field=field_name,
                    database=stored,
                    mcp=returned,
                )
            )
    if include_internal_semantics:
        _compare_definition_formula_semantics(definition, detail, mcp_detail, issues)
    else:
        _compare_definition_result_projection(definition, detail, mcp_detail, issues)

    current_versions = {
        "database_detail": _formula_version_from_detail(detail),
        "mcp_detail": _formula_version_from_mapping(mcp_detail),
    }
    versions = {value for value in current_versions.values() if value is not None}
    if len(versions) > 1:
        issues.append(
            _failed(
                "CURRENT_FORMULA_VERSION_MISMATCH",
                "数据库最新 detail 与 MCP 当前详情的 formula_version 不一致。",
                definition.factor_ref,
                versions=current_versions,
            )
        )

    if detail is not None:
        for container_name, stored in (("params", detail.params), ("data_source_metadata", detail.data_source_metadata)):
            returned = mcp_detail.get(container_name)
            if not isinstance(stored, Mapping) or not isinstance(returned, Mapping):
                continue
            for key in ("fields", "declared_fields", "required_fields", "resolved_raw_fields", "input_fields"):
                stored_fields, returned_fields = stored.get(key), returned.get(key)
                if not isinstance(stored_fields, (list, tuple)) or not isinstance(returned_fields, (list, tuple)):
                    continue
                if set(stored_fields) != set(returned_fields):
                    issues.append(_failed(
                        "CURRENT_FORMULA_FIELDS_MISMATCH",
                        "数据库最新 detail 与 MCP 当前详情声明的输入字段不一致。",
                        definition.factor_ref,
                        field=f"{container_name}.{key}",
                        database=sorted(str(item) for item in stored_fields),
                        mcp=sorted(str(item) for item in returned_fields),
                    ))


def _projection_expressions_equivalent(
    stored: Any, returned: Any, *, variables: Mapping[str, int] | None = None,
) -> bool:
    """Compare producer formula projections without proving operator mathematics.

    Retain assignment wrappers, declared window substitution and integer constant
    normalization used by historical output checks. Unknown operators can compare
    equal; this deliberately does not establish their computation or lookback.
    """
    if not isinstance(stored, str) or not isinstance(returned, str):
        return stored == returned
    if stored.strip() == returned.strip():
        return True
    left, right = _formula_expression_node(stored), _formula_expression_node(returned)
    if left is None or right is None:
        return False
    normalizer = _FormulaExpressionNormalizer(variables)
    return ast.dump(normalizer.visit(left), include_attributes=False) == ast.dump(
        normalizer.visit(right), include_attributes=False,
    )


def _compare_definition_result_projection(
    definition: FactorDefinition, detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any], issues: list[CalculationIssue],
) -> None:
    """Compare corresponding stored/public formula fields, not their mathematical meaning."""
    window = _declared_window((definition,), (detail,) if detail else (), mcp_detail)
    variables = {"window": window} if window is not None else None
    if detail is not None and not _projection_expressions_equivalent(
        detail.calc_logic, mcp_detail.get("calc_logic"), variables=variables,
    ):
        issues.append(_failed("MCP_DETAIL_FORMULA_MISMATCH",
                              "MCP 可执行详情与数据库当前公式表达式不一致。", definition.factor_ref))
    if definition.formula_summary is not None and mcp_detail.get("formula_summary") is not None:
        if not _projection_expressions_equivalent(
            definition.formula_summary, mcp_detail["formula_summary"], variables=variables,
        ):
            issues.append(_failed("MCP_DETAIL_PROJECTION_MISMATCH",
                                  "MCP 详情与数据库当前定义字段不一致。", definition.factor_ref,
                                  field="formula_summary"))
    if detail is None:
        return
    for container_name, stored in (("params", detail.params), ("data_source_metadata", detail.data_source_metadata)):
        returned = mcp_detail.get(container_name)
        if not isinstance(stored, Mapping) or not isinstance(returned, Mapping):
            continue
        for key in ("normalized_formula", "calc_logic", "formula", "expression"):
            if key in stored and key in returned and not _projection_expressions_equivalent(
                stored[key], returned[key], variables=variables,
            ):
                issues.append(_failed("MCP_DETAIL_PROJECTION_MISMATCH",
                                      "MCP 详情与数据库当前定义字段不一致。", definition.factor_ref,
                                      field=f"{container_name}.{key}"))


def _compare_definition_formula_semantics(
    definition: FactorDefinition, detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any], issues: list[CalculationIssue],
) -> None:
    """Preserve historical AST/equivalence judgments outside result-only checks."""
    declared_window = _declared_window(
        (definition,),
        (detail,) if detail is not None else (),
        mcp_detail,
    )
    variables = {"window": declared_window} if declared_window is not None else None
    definition_formulas = _definition_formula_candidates(definition, variables=variables)
    detail_formulas = _detail_formula_candidates(detail, variables=variables)
    mcp_formulas = _mcp_formula_candidates(mcp_detail, variables=variables)
    if definition_formulas and detail_formulas and not _candidate_sets_equivalent(
        definition_formulas,
        detail_formulas,
        variables=variables,
    ):
        issues.append(
            _failed(
                "DB_DETAIL_FORMULA_MISMATCH",
                "数据库当前定义与最新 detail 的公式表达式不一致。",
                definition.factor_ref,
            )
        )
    db_formulas = detail_formulas
    if db_formulas and mcp_formulas and not _candidate_sets_equivalent(
        db_formulas,
        mcp_formulas,
        variables=variables,
    ):
        issues.append(
            _failed(
                "MCP_DETAIL_FORMULA_MISMATCH",
                "MCP 可执行详情与数据库当前公式表达式不一致。",
                definition.factor_ref,
            )
        )
    _compare_normalized_formula_metadata(
        definition.factor_ref, detail, mcp_detail, issues, variables=variables,
    )
    if _candidate_conflicts(definition_formulas, variables=variables) or _candidate_conflicts(
        detail_formulas,
        variables=variables,
    ) or _candidate_conflicts(mcp_formulas, variables=variables):
        issues.append(
            _failed(
                "CURRENT_FORMULA_SOURCE_CONFLICT",
                "同一当前来源包含多个可解析且语义不同的公式表达式。",
                definition.factor_ref,
            )
        )

def _compare_formula_projection(
    evidence: FormulaEvidence,
    returned: Mapping[str, Any],
    issues: list[CalculationIssue],
    *,
    include_internal_semantics: bool = True,
) -> None:
    expected_identity = {
        "factor_ref": evidence.factor_ref,
        "run_id": evidence.run_id,
        "formula_hash": evidence.formula_hash,
        "formula_version": evidence.formula_version,
        "source_detail_id": evidence.source_detail_id,
    }
    for name, expected in expected_identity.items():
        if returned.get(name) != expected:
            issues.append(
                _failed(
                    "MCP_FORMULA_PROJECTION_MISMATCH",
                    "MCP 精确 Run 公式身份与数据库 evidence 不一致。",
                    evidence.factor_ref,
                    field=name,
                    database=expected,
                    mcp=returned.get(name),
                )
            )
    evidence_window = _positive_window(evidence.factor_window_bars)
    variables = {"window": evidence_window} if evidence_window is not None else None
    expression_matches = (
        _expressions_equivalent(returned.get("expression"), evidence.expression, variables=variables)
        if include_internal_semantics
        else _projection_expressions_equivalent(evidence.expression, returned.get("expression"), variables=variables)
    )
    if not expression_matches:
        issues.append(
            _failed(
                "MCP_FORMULA_EXPRESSION_MISMATCH",
                "MCP 精确 Run 表达式与数据库 evidence 不一致。",
                evidence.factor_ref,
                run_id=evidence.run_id,
            )
        )
    returned_fields = returned.get("required_fields")
    if not isinstance(returned_fields, list):
        issues.append(
            _blocked(
                "MCP_FORMULA_REQUIRED_FIELDS_MISSING",
                "MCP 公式证据缺少 required_fields。",
                evidence.factor_ref,
                run_id=evidence.run_id,
            )
        )
    elif {str(value) for value in returned_fields} != {str(value) for value in evidence.required_fields}:
        issues.append(
            _failed(
                "MCP_FORMULA_REQUIRED_FIELDS_MISMATCH",
                "MCP required_fields 与数据库 evidence 不一致。",
                evidence.factor_ref,
                run_id=evidence.run_id,
            )
        )
    metric_identity = returned.get("metric_identity")
    expected_metric_identity = {
        "calculation_mode": evidence.calculation_mode,
        "factor_bar_interval": evidence.factor_bar_interval,
        "factor_window_bars": evidence.factor_window_bars,
        "return_bar_interval": evidence.return_bar_interval,
        "forward_return_bars": evidence.forward_return_bars,
    }
    if not isinstance(metric_identity, Mapping):
        issues.append(
            _blocked(
                "MCP_FORMULA_METRIC_IDENTITY_MISSING",
                "MCP 公式证据缺少完整 metric_identity。",
                evidence.factor_ref,
                run_id=evidence.run_id,
            )
        )
    else:
        for name, expected in expected_metric_identity.items():
            if metric_identity.get(name) != expected:
                issues.append(
                    _failed(
                        "MCP_FORMULA_METRIC_IDENTITY_MISMATCH",
                        "MCP 公式 metric_identity 与数据库 evidence 不一致。",
                        evidence.factor_ref,
                        field=name,
                        database=expected,
                        mcp=metric_identity.get(name),
                    )
                )


def _definition_schema_fields(
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any] | None,
) -> set[str]:
    fields: set[str] = set()
    values: list[Any] = []
    if detail is not None:
        values.extend((detail.params, detail.data_source_metadata))
    if mcp_detail is not None:
        values.extend(
            (
                mcp_detail.get("params"),
                mcp_detail.get("metadata"),
                mcp_detail.get("data_source_metadata"),
            )
        )
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for key in ("fields", "declared_fields", "required_fields", "resolved_raw_fields", "derived_fields"):
            items = value.get(key)
            if isinstance(items, (list, tuple, set)):
                fields.update(str(item) for item in items if str(item))
        nested = value.get("data_source_metadata")
        if isinstance(nested, Mapping):
            fields.update(_definition_schema_fields(None, nested))
    return fields


def _declared_formula_fields(detail: FactorDetail | None) -> set[str]:
    """Extract only the explicit input-field declaration from a DB detail."""

    if detail is None:
        return set()
    return _fields_from_mapping(detail.params) | _fields_from_mapping(
        detail.data_source_metadata
    )


def _mapping_declared_formula_fields(value: Mapping[str, Any] | None) -> set[str]:
    """Extract explicit input fields from an MCP detail projection."""

    if not isinstance(value, Mapping):
        return set()
    fields = _fields_from_mapping(value)
    for key in ("params", "metadata", "data_source_metadata"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            fields.update(_fields_from_mapping(nested))
    return fields


def _fields_from_mapping(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    fields: set[str] = set()
    for key in (
        "fields",
        "declared_fields",
        "required_fields",
        "resolved_raw_fields",
        "input_fields",
    ):
        items = value.get(key)
        if isinstance(items, (list, tuple, set)):
            fields.update(str(item) for item in items if str(item).strip())
    return fields


def _formula_version_from_mapping(value: Mapping[str, Any] | None) -> str | None:
    """Read a formula version from known MCP detail metadata locations."""

    if not isinstance(value, Mapping):
        return None
    direct = value.get("formula_version")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key in ("params", "metadata", "data_source_metadata"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            nested_version = _formula_version_from_mapping(nested)
            if nested_version is not None:
                return nested_version
    return None


def _formula_version_from_detail(detail: FactorDetail | None) -> str | None:
    if detail is None:
        return None
    for value in (detail.params, detail.data_source_metadata):
        version = _formula_version_from_mapping(value)
        if version is not None:
            return version
    return None


def _definition_version_candidates(
    definition: FactorDefinition | None,
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any] | None,
) -> tuple[str, tuple[Any, ...]]:
    """Return the frozen definition version and observable current versions.

    The catalog snapshot uses ``updated_at:<ISO timestamp>`` for its frozen
    definition version.  Current detail/MCP projections may expose the same
    value under ``definition_factor_version``/``definition_version`` or only
    expose an ``updated_at`` timestamp.  Executable ``factor_version`` hashes
    are intentionally ignored here because they belong to formula execution,
    not catalog membership.
    """

    frozen: str | None = None
    if definition is not None and definition.batch_factor_version.strip():
        frozen = definition.batch_factor_version.strip()
    elif detail is not None and detail.batch_factor_version.strip():
        frozen = detail.batch_factor_version.strip()
    observed: list[Any] = []
    if (
        frozen is not None
        and frozen.casefold().startswith("updated_at:")
        and definition is not None
        and definition.definition_updated_at is not None
    ):
        observed.append(definition.definition_updated_at)
    if (
        frozen is not None
        and frozen.casefold().startswith("updated_at:")
        and detail is not None
        and detail.updated_at is not None
    ):
        observed.append(detail.updated_at)
    if isinstance(mcp_detail, Mapping):
        version_keys = (
            "definition_factor_version",
            "definition_version",
            "catalog_version",
        )
        for key in version_keys + (("updated_at",) if frozen and frozen.casefold().startswith("updated_at:") else ()):
            value = mcp_detail.get(key)
            if _identity_value_present(value):
                observed.append(value)
        for container_name in ("metadata", "params", "data_source_metadata"):
            container = mcp_detail.get(container_name)
            if not isinstance(container, Mapping):
                continue
            for key in version_keys + (("updated_at",) if frozen and frozen.casefold().startswith("updated_at:") else ()):
                value = container.get(key)
                if _identity_value_present(value):
                    observed.append(value)
    return frozen or "", tuple(observed)


def _same_definition_version(frozen: str, observed: Any) -> bool | None:
    """Compare a frozen catalog version with one current projection value."""

    if not _identity_value_present(observed):
        return None
    frozen_text = frozen.strip()
    if frozen_text.casefold().startswith("updated_at:"):
        frozen_raw = frozen_text.split(":", 1)[1].strip()
        if frozen_raw.endswith("Z"):
            frozen_raw = frozen_raw[:-1] + "+00:00"
        try:
            frozen_time = datetime.fromisoformat(frozen_raw)
        except ValueError:
            return None
        if isinstance(observed, datetime):
            observed_time = observed
        else:
            observed_text = str(observed).strip()
            if observed_text.casefold().startswith("updated_at:"):
                observed_text = observed_text.split(":", 1)[1].strip()
            if observed_text.endswith("Z"):
                observed_text = observed_text[:-1] + "+00:00"
            try:
                observed_time = datetime.fromisoformat(observed_text)
            except ValueError:
                return None
        return _datetime_sort_key(frozen_time) == _datetime_sort_key(observed_time)
    observed_text = str(observed).strip()
    return frozen_text.casefold() == observed_text.casefold()


def _current_definition_version_status(
    definition: FactorDefinition | None,
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any] | None,
) -> bool | None:
    """Return current-vs-frozen definition status, or ``None`` if unprovable."""

    frozen, observed = _definition_version_candidates(definition, detail, mcp_detail)
    if not frozen or not observed:
        return None
    comparisons = [_same_definition_version(frozen, value) for value in observed]
    known = [value for value in comparisons if value is not None]
    if not known:
        return None
    return all(known)


def _compare_current_formula_identity(
    evidence: FormulaEvidence,
    definition: FactorDefinition | None,
    detail: FactorDetail | None,
    mcp_detail: Mapping[str, Any] | None,
    issues: list[CalculationIssue],
) -> None:
    """Compare a batch-bound evidence identity with current catalog metadata."""

    if definition is None and detail is None and mcp_detail is None:
        return
    definition_status = _current_definition_version_status(
        definition,
        detail,
        mcp_detail,
    )
    if definition_status is False:
        issues.append(
            _blocked(
                "CURRENT_DEFINITION_VERSION_DRIFT",
                "当前目录定义版本与发布批次冻结版本不同，不能用当前公式反推批次执行公式。",
                evidence.factor_ref,
                frozen_definition_version=(
                    definition.batch_factor_version
                    if definition is not None
                    else detail.batch_factor_version
                    if detail is not None
                    else None
                ),
            )
        )
        return
    frozen_definition_version, _ = _definition_version_candidates(
        definition,
        detail,
        mcp_detail,
    )
    if definition_status is None and frozen_definition_version.casefold().startswith(
        "updated_at:"
    ):
        issues.append(
            _blocked(
                "CURRENT_DEFINITION_VERSION_UNRESOLVED",
                "无法确认当前目录定义与发布批次冻结版本相同，跳过跨时态公式等值断言。",
                evidence.factor_ref,
            )
        )
        return
    current_version = _formula_version_from_detail(detail) or _formula_version_from_mapping(
        mcp_detail
    )
    if current_version is not None and current_version != evidence.formula_version:
        issues.append(
            _failed(
                "CURRENT_BATCH_FORMULA_VERSION_MISMATCH",
                "batch-bound formula evidence 的版本与当前 detail/MCP formula_version 不一致。",
                evidence.factor_ref,
                evidence_formula_version=evidence.formula_version,
                current_formula_version=current_version,
            )
        )
    current_fields = _declared_formula_fields(detail) | _mapping_declared_formula_fields(
        mcp_detail
    )
    required = {str(value) for value in evidence.required_fields if str(value).strip()}
    if current_fields and required and not required.issubset(current_fields):
        issues.append(
            _failed(
                "CURRENT_BATCH_FORMULA_FIELDS_MISMATCH",
                "batch-bound formula evidence 的输入字段未被当前 detail/MCP 声明完整覆盖。",
                evidence.factor_ref,
                missing_fields=sorted(required - current_fields),
            )
        )


def _check_metric_formula_link(
    metric: EvaluationMetric,
    evidence: Sequence[FormulaEvidence],
    issues: list[CalculationIssue],
) -> FormulaEvidence | None:
    """Resolve one metric to an immutable formula evidence row.

    ``source_detail_id`` is deliberately treated as a trace-only field.  It
    identifies a mutable detail row and therefore cannot prove which formula
    was executed for a published metric.  At least one immutable identity
    field (hash, version, run ID, or evidence ID) is required.  Context fields
    are applied before deciding whether a hash/version link is unique.  A
    missing or ambiguous candidate is a data precondition rather than a
    formula failure; a resolved row whose explicit trace or version fields
    contradict the metric is reported separately.
    """

    identity = metric.metric_identity
    if identity is None:
        issues.append(
            _blocked(
                "METRIC_IDENTITY_MISSING",
                "batch metric 缺少 metric_identity，不能证明公式版本链。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
        issues.append(
            _blocked(
                "METRIC_FORMULA_VERSION_LINK_MISSING",
                "batch metric 没有 formula_hash/formula_version/run_id，不能绑定到 completed formula evidence。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
        return None
    if not isinstance(identity, Mapping):
        issues.append(
            _blocked(
                "METRIC_FORMULA_LINK_INVALID",
                "metric_identity 不是对象，不能读取公式链接。",
                metric.factor_ref,
                metric_id=metric.id,
                invalid_fields=["metric_identity"],
                invalid_reasons=["identity_must_be_object"],
                invalid_sources=["metric_identity"],
            )
        )
        return None
    link_identity, invalid_links = _validated_metric_formula_identity(identity)
    if invalid_links:
        fields = sorted({field for field, _source, _reason in invalid_links})
        reasons = sorted({reason for _field, _source, reason in invalid_links})
        sources = sorted({source for _field, source, _reason in invalid_links})
        issues.append(
            _blocked(
                "METRIC_FORMULA_LINK_INVALID",
                "metric_identity 中的公式链接包含非法值或互相冲突，不能绑定到 completed formula evidence。",
                metric.factor_ref,
                metric_id=metric.id,
                invalid_fields=fields[:_ISSUE_SAMPLE_LIMIT],
                invalid_reasons=reasons[:_ISSUE_SAMPLE_LIMIT],
                invalid_sources=sources[:_ISSUE_SAMPLE_LIMIT],
            )
        )
        return None
    if (
        _identity_value_present(link_identity.get("factor_version"))
        and not _same_formula_identity_value(
            link_identity.get("factor_version"),
            metric.factor_version,
        )
    ):
        issues.append(
            _failed(
                "METRIC_FACTOR_VERSION_MISMATCH",
                "metric_identity.factor_version 与冻结 factor_version 不一致。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
    present_strong_links = {
        name: link_identity[name]
        for name in _STRONG_FORMULA_LINK_NAMES
        if _identity_value_present(link_identity.get(name))
    }
    source_detail_id = link_identity.get(_SOURCE_DETAIL_LINK_NAME)
    if not _identity_value_present(source_detail_id):
        source_detail_id = None
    if not present_strong_links:
        if source_detail_id is not None:
            issues.append(
                _blocked(
                    "METRIC_FORMULA_LINK_SOURCE_DETAIL_ONLY",
                    "batch metric 只有 source_detail_id；该字段不能证明实际执行的不可变公式版本。",
                    metric.factor_ref,
                    metric_id=metric.id,
                    source_detail_id=source_detail_id,
                )
            )
        else:
            issues.append(
                _blocked(
                    "METRIC_FORMULA_VERSION_LINK_MISSING",
                    "batch metric 没有 formula_hash/formula_version/run_id，不能绑定到 completed formula evidence。",
                    metric.factor_ref,
                    metric_id=metric.id,
                )
            )
        return None

    # The Repository may return more than one completed row for a factor.  Do
    # not use source_detail_id to select one: it is mutable trace metadata.
    candidates = [
        row
        for row in evidence
        if (
            row.factor_ref == metric.factor_ref
            and row.factor_type == metric.factor_type
            and row.factor_id == metric.factor_id
        )
    ]
    for name, value in present_strong_links.items():
        evidence_name = "id" if name == "formula_evidence_id" else name
        candidates = [
            row
            for row in candidates
            if _same_formula_identity_value(getattr(row, evidence_name), value)
        ]
    linked_candidates = candidates
    context_candidates = [
        row
        for row in linked_candidates
        if not _metric_formula_context_mismatches(metric, row, link_identity)
    ]
    if context_candidates:
        candidates = context_candidates

    if len(candidates) == 0:
        issues.append(
            _blocked(
                "METRIC_FORMULA_EVIDENCE_MISSING",
                "batch metric 声明了不可变公式链接，但当前快照没有唯一对应的 completed evidence。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
        return None
    if len(candidates) > 1:
        issues.append(
            _blocked(
                "METRIC_FORMULA_LINK_NOT_UNIQUE",
                "batch metric 的公式 hash/version 链接在完整上下文下仍不能唯一命中 evidence；不能猜选一条执行记录。",
                metric.factor_ref,
                metric_id=metric.id,
                matched_count=len(candidates),
            )
        )
        return None

    resolved = candidates[0]
    if (
        str(resolved.run_status).casefold() != "completed"
        or resolved.run_completed_at is None
    ):
        issues.append(
            _blocked(
                "FORMULA_EVIDENCE_NOT_COMPLETED",
                "metric 链接到的公式 evidence 不是 completed run，不能作为批次执行证据。",
                metric.factor_ref,
                metric_id=metric.id,
                evidence_id=resolved.id,
                run_id=resolved.run_id,
                run_status=resolved.run_status,
            )
        )
        return None
    if (
        resolved.factor_ref != metric.factor_ref
        or resolved.factor_type != metric.factor_type
        or resolved.factor_id != metric.factor_id
    ):
        issues.append(
            _failed(
                "METRIC_FORMULA_EVIDENCE_FACTOR_MISMATCH",
                "metric 链接到的公式 evidence 因子身份不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                evidence_id=resolved.id,
            )
        )
    declared_definition_version = link_identity.get("definition_factor_version")
    if (
        _identity_value_present(declared_definition_version)
        and not _same_formula_identity_value(
            resolved.batch_factor_version,
            declared_definition_version,
        )
    ):
        issues.append(
            _failed(
                "METRIC_FORMULA_DEFINITION_VERSION_MISMATCH",
                "metric 的 definition_factor_version 与冻结成员版本不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                evidence_id=resolved.id,
                metric_definition_factor_version=declared_definition_version,
                frozen_definition_factor_version=resolved.batch_factor_version,
            )
        )
    if (
        resolved.formula_factor_version is not None
        and not _same_formula_identity_value(
            resolved.formula_factor_version,
            metric.factor_version,
        )
    ):
        issues.append(
            _failed(
                "METRIC_FORMULA_EXECUTABLE_VERSION_MISMATCH",
                "metric 的可执行 factor_version 与公式 evidence 版本不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                evidence_id=resolved.id,
                metric_factor_version=metric.factor_version,
                evidence_factor_version=resolved.formula_factor_version,
            )
        )
    if (
        source_detail_id is not None
        and not _same_formula_identity_value(resolved.source_detail_id, source_detail_id)
    ):
        issues.append(
            _failed(
                "METRIC_FORMULA_SOURCE_DETAIL_MISMATCH",
                "metric 的 source_detail_id 与已解析公式 evidence 不一致；该字段只能作为 trace 对账。",
                metric.factor_ref,
                metric_id=metric.id,
                evidence_id=resolved.id,
                metric_source_detail_id=source_detail_id,
                evidence_source_detail_id=resolved.source_detail_id,
            )
        )
    context_mismatches = _metric_formula_context_mismatches(
        metric,
        resolved,
        link_identity,
    )
    if context_mismatches:
        issues.append(
            _failed(
                "METRIC_FORMULA_CONTEXT_MISMATCH",
                "batch metric 的计算上下文与绑定公式 evidence 不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                mismatches=context_mismatches,
            )
        )
    return resolved


def _normalized_metric_formula_identity(
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Flatten supported nested formula-link containers and aliases.

    This compatibility helper keeps the historical mapping-only API.  The
    Service uses :func:`_validated_metric_formula_identity` before calling it,
    so conflicting or malformed values never reach candidate resolution.
    """

    normalized, _invalid = _validated_metric_formula_identity(identity)
    return normalized


def _validated_metric_formula_identity(
    identity: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[tuple[str, str, str], ...]]:
    """Normalize formula links and report malformed or contradictory values.

    Top-level fields and the supported ``formula``/``formula_identity``/
    ``formula_evidence`` objects are compatible representations.  A nested
    value fills an omitted top-level value; equal duplicates are accepted.
    Any non-scalar link, invalid numeric ID, malformed container, or unequal
    duplicate is returned as ``(field, source, reason)`` so callers can block
    without guessing which representation wins.
    """

    normalized = dict(identity)
    occurrences: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    invalid: list[tuple[str, str, str]] = []

    def add_occurrence(raw_name: str, value: Any, source: str) -> None:
        canonical = (
            "formula_evidence_id"
            if raw_name == "evidence_id"
            else raw_name
        )
        occurrences[canonical].append((source, value))

    for raw_name in _FORMULA_LINK_FIELD_NAMES:
        if raw_name in identity:
            add_occurrence(raw_name, identity[raw_name], f"top_level.{raw_name}")

    for container_name in _FORMULA_LINK_CONTAINER_NAMES:
        if container_name not in identity:
            continue
        nested = identity[container_name]
        if nested is None:
            # Explicit null means the optional compatibility container is not
            # present; it does not override a valid top-level link.
            continue
        if not isinstance(nested, Mapping):
            invalid.append((container_name, container_name, "container_must_be_object"))
            continue
        for raw_name in _FORMULA_LINK_FIELD_NAMES:
            if raw_name in nested:
                add_occurrence(
                    raw_name,
                    nested[raw_name],
                    f"{container_name}.{raw_name}",
                )

    for canonical, values in occurrences.items():
        valid_values: list[tuple[str, Any]] = []
        for source, value in values:
            if not _identity_value_present(value):
                # ``null`` and blank compatibility fields retain the existing
                # missing-link semantics; a non-blank representation can fill
                # them from another supported container.
                continue
            reason = _formula_link_value_error(canonical, value)
            if reason is not None:
                invalid.append((canonical, source, reason))
            else:
                valid_values.append((source, value))
        if not valid_values:
            continue
        first_source, first_value = valid_values[0]
        for source, value in valid_values[1:]:
            if canonical in _FORMULA_LINK_ID_NAMES:
                same_value = _positive_integer_identity(first_value) == _positive_integer_identity(value)
            else:
                same_value = _same_formula_identity_value(first_value, value)
            if not same_value:
                invalid.append((canonical, f"{first_source}|{source}", "conflicting_values"))
        # Do not silently let a top-level value win.  All equal values are
        # interchangeable; retaining the first only affects the normalized
        # internal representation, never the validation decision.
        normalized[canonical] = (
            _positive_integer_identity(first_value)
            if canonical in _FORMULA_LINK_ID_NAMES
            else first_value
        )

    # ``evidence_id`` is a documented alias.  Keep the original key for
    # diagnostics but expose the canonical key to the resolver.
    if "formula_evidence_id" in normalized:
        normalized.pop("evidence_id", None)
    return normalized, tuple(invalid)


def _formula_link_value_error(name: str, value: Any) -> str | None:
    """Return a stable reason when one formula-link value is malformed."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return "boolean_not_allowed"
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, set)):
        return "value_must_be_scalar"
    if isinstance(value, (float, Decimal)):
        # Decimal handles NaN/Infinity without introducing a new dependency.
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            return "non_finite_number"
        if not decimal_value.is_finite():
            return "non_finite_number"
    elif not isinstance(value, (str, int, float, Decimal)):
        return "unsupported_scalar_type"
    if name in _FORMULA_LINK_ID_NAMES and _positive_integer_identity(value) is None:
        return "must_be_positive_integer"
    if name not in _FORMULA_LINK_ID_NAMES and not isinstance(value, str):
        return "must_be_non_empty_string"
    return None


def _positive_integer_identity(value: Any) -> int | None:
    """Parse an ID without truncating fractional or boolean values."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not re.fullmatch(r"\+?\d+", text):
                return None
            parsed = int(text)
        elif isinstance(value, (int, float, Decimal)):
            decimal_value = Decimal(str(value))
            if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
                return None
            parsed = int(decimal_value)
        else:
            return None
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _identity_value_present(value: Any) -> bool:
    """Return whether a JSON identity field contains a meaningful value."""

    return value is not None and not (
        isinstance(value, str) and not value.strip()
    )


def _same_formula_identity_value(stored: Any, declared: Any) -> bool:
    """Compare JSON identity values without conflating booleans and numbers."""

    if isinstance(stored, bool) or isinstance(declared, bool):
        return stored is declared
    if isinstance(stored, (int, float, Decimal)) and isinstance(
        declared, (int, float, Decimal)
    ):
        return Decimal(str(stored)) == Decimal(str(declared))
    if stored is None or declared is None:
        return stored is declared
    return str(stored).strip().casefold() == str(declared).strip().casefold()


def _same_formula_context_value(name: str, stored: Any, declared: Any) -> bool:
    """Compare context fields with bar-window and interval normalization."""

    if name == "factor_window_bars":
        stored_window = _positive_window(stored)
        declared_window = _positive_window(declared)
        if stored_window is not None and declared_window is not None:
            return stored_window == declared_window
    if name in {"factor_bar_interval", "return_bar_interval"}:
        return str(stored).strip().casefold() == str(declared).strip().casefold()
    return _same_formula_identity_value(stored, declared)


def _metric_formula_context_expected(
    metric: EvaluationMetric,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the persisted context used to resolve a formula evidence row."""

    expected: dict[str, Any] = {
        "return_bar_interval": metric.return_bar_interval,
        "forward_return_bars": metric.forward_return_bars,
    }
    # The metric schema calls the factor sampling interval ``interval`` while
    # formula evidence calls it ``factor_bar_interval``.  Prefer an explicit
    # identity value when present and otherwise use the persisted metric field.
    identity_interval = identity.get("factor_bar_interval")
    expected["factor_bar_interval"] = (
        identity_interval
        if _identity_value_present(identity_interval)
        else metric.interval
    )
    if identity.get("factor_window_bars") is not None:
        expected["factor_window_bars"] = identity["factor_window_bars"]
    if identity.get("calculation_mode") is not None:
        expected["calculation_mode"] = identity["calculation_mode"]
    return expected


def _metric_formula_context_mismatches(
    metric: EvaluationMetric,
    evidence: FormulaEvidence,
    identity: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return explicit context differences between a metric and evidence row."""

    expected = _metric_formula_context_expected(metric, identity)
    actual = {
        "return_bar_interval": evidence.return_bar_interval,
        "forward_return_bars": evidence.forward_return_bars,
        "factor_bar_interval": evidence.factor_bar_interval,
        "factor_window_bars": evidence.factor_window_bars,
        "calculation_mode": evidence.calculation_mode,
    }
    return {
        name: {"metric": value, "evidence": actual[name]}
        for name, value in expected.items()
        if actual.get(name) is not None
        and not _same_formula_context_value(name, actual[name], value)
    }


def _compare_metric_formula_context(
    metric: EvaluationMetric,
    evidence: FormulaEvidence,
    identity: Mapping[str, Any],
    issues: list[CalculationIssue],
) -> None:
    """Check the non-link metric context against its resolved formula row."""

    mismatches = _metric_formula_context_mismatches(metric, evidence, identity)
    if mismatches:
        issues.append(
            _failed(
                "METRIC_FORMULA_CONTEXT_MISMATCH",
                "batch metric 的计算上下文与绑定公式 evidence 不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                mismatches=mismatches,
            )
        )


def _collect_formula_expressions(
    factor_ref: str,
    definitions: Sequence[FactorDefinition],
    details: Sequence[FactorDetail],
    evidence: Sequence[FormulaEvidence],
    mcp_detail: Mapping[str, Any] | None,
    formula_cache: Mapping[tuple[Any, ...], Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """Collect expressions from explicitly selected source layers.

    Callers pass only the latest detail and batch-bound evidence.  The helper
    still defensively collapses a wider detail sequence so an old mutable row
    cannot become a false current-regression failure.
    """

    del factor_ref
    expressions: list[tuple[str, str]] = []
    # Definition summaries are compared with current detail by the projection
    # audit, but are not an executed formula source for a known-regression oracle.
    del definitions
    latest_detail = _latest_detail(details)
    selected_details: Sequence[FactorDetail] = (
        (latest_detail,) if latest_detail is not None else ()
    )
    for detail in selected_details:
        declared = None
        if isinstance(detail.params, Mapping):
            for key in ("declared_window", "window", "code_variable"):
                declared = _positive_window(detail.params.get(key))
                if declared is not None:
                    break
        variables = {"window": declared} if declared is not None else None
        expressions.extend(_detail_formula_candidates(detail, variables=variables))
    for index, formula in enumerate(evidence):
        # Formula evidence is expected to be executable; retain malformed
        # values so the known-regression oracle fails closed instead of
        # silently treating them as prose.
        if isinstance(formula.expression, str) and formula.expression.strip():
            declared = _positive_window(formula.factor_window_bars)
            variables = {"window": declared} if declared is not None else None
            candidate = _formula_candidate(
                f"batch.db_formula_evidence[{index}]",
                formula.expression,
                variables=variables,
            )
            # Preserve a non-empty but malformed expression for the known
            # regression oracle: its semantic predicate will return False and
            # surface the defect instead of silently dropping the source.
            expressions.append(
                candidate
                or (f"batch.db_formula_evidence[{index}]", formula.expression)
            )
        returned = formula_cache.get(_formula_key(formula))
        if returned and isinstance(returned.get("expression"), str):
            declared = _positive_window(formula.factor_window_bars)
            variables = {"window": declared} if declared is not None else None
            expression = str(returned["expression"])
            candidate = _formula_candidate(
                f"batch.mcp_formula[{index}]",
                expression,
                variables=variables,
            )
            expressions.append(candidate or (f"batch.mcp_formula[{index}]", expression))
    if mcp_detail is not None:
        declared = _positive_window(mcp_detail.get("window"))
        params = mcp_detail.get("params")
        if declared is None and isinstance(params, Mapping):
            for key in ("declared_window", "window", "code_variable"):
                declared = _positive_window(params.get(key))
                if declared is not None:
                    break
        variables = {"window": declared} if declared is not None else None
        expressions.extend(_mcp_formula_candidates(mcp_detail, variables=variables))
    return expressions


def _positive_window(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = re.search(r"(?<!\d)(\d+)(?!\d)", str(value))
    if match is None:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def _declared_window(
    definitions: Sequence[FactorDefinition],
    details: Sequence[FactorDetail],
    mcp_detail: Mapping[str, Any] | None,
) -> int | None:
    candidates: set[int] = set()
    latest_definition = _latest_definition(definitions)
    for definition in (latest_definition,) if latest_definition is not None else ():
        parsed = _positive_window(definition.window)
        if parsed is not None:
            candidates.add(parsed)
    latest = _latest_detail(details)
    for detail in (latest,) if latest is not None else ():
        if isinstance(detail.params, Mapping):
            for key in ("declared_window", "window", "code_variable"):
                parsed = _positive_window(detail.params.get(key))
                if parsed is not None:
                    candidates.add(parsed)
    if isinstance(mcp_detail, Mapping):
        for key in ("declared_window", "window"):
            parsed = _positive_window(mcp_detail.get(key))
            if parsed is not None:
                candidates.add(parsed)
        params = mcp_detail.get("params")
        if isinstance(params, Mapping):
            for key in ("declared_window", "window", "code_variable"):
                parsed = _positive_window(params.get(key))
                if parsed is not None:
                    candidates.add(parsed)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _node_static_int(node: ast.AST, variables: Mapping[str, int]) -> int | None:
    if isinstance(node, ast.Name):
        # Formula sources in the catalog are not consistent about the case of
        # parameter names (``window`` vs ``WINDOW``). Treat declared variable
        # names case-insensitively, matching the AST normalizer used elsewhere.
        value = variables.get(node.id)
        if value is not None:
            return value
        folded = node.id.casefold()
        for name, declared in variables.items():
            if str(name).casefold() == folded:
                return declared
        return None
    try:
        return _static_int(node)
    except FormulaOffsetError:
        if isinstance(node, ast.BinOp):
            left = _node_static_int(node.left, variables)
            right = _node_static_int(node.right, variables)
            if left is None or right is None:
                return None
            try:
                expression = ast.Expression(
                    ast.BinOp(ast.Constant(left), node.op, ast.Constant(right))
                )
                return _static_int(expression.body)
            except FormulaOffsetError:
                return None
        return None


def _is_name(node: ast.AST, expected: str) -> bool:
    return isinstance(node, ast.Name) and node.id.casefold() == expected.casefold()


def _is_shift(node: ast.AST, field_name: str, period: int, variables: Mapping[str, int]) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.casefold() == "shift"
        and _is_name(node.func.value, field_name)
    ):
        return False
    return _method_call_period(node, variables) == period


def _is_rolling_mean(node: ast.AST, field_name: str, window: int, variables: Mapping[str, int]) -> bool:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.casefold() == "mean"
    ):
        period_node: ast.AST | None = node.args[1] if len(node.args) >= 2 else None
        if period_node is None:
            period_node = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if (
                        keyword.arg is not None
                        and keyword.arg.casefold() in {"window", "period", "periods"}
                    )
                ),
                None,
            )
        return (
            bool(node.args)
            and period_node is not None
            and _is_name(node.args[0], field_name)
            and _node_static_int(period_node, variables) == window
        )
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.casefold() == "mean"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Attribute)
        and node.func.value.func.attr.casefold() == "rolling"
        and _is_name(node.func.value.func.value, field_name)
        and _method_call_period(node.func.value, variables) == window
    )


def _is_correct_dpo(expression: str, window: int) -> bool:
    body = _formula_expression_node(expression)
    if body is None:
        return False
    shift = window // 2 + 1
    variables = {"window": window}
    if isinstance(body, ast.BinOp) and isinstance(body.op, ast.Sub):
        return _is_rolling_mean(body.left, "close", window, variables) and _is_shift(
            body.right, "close", shift, variables
        )
    if isinstance(body, ast.UnaryOp) and isinstance(body.op, ast.USub):
        operand = body.operand
        return (
            isinstance(operand, ast.BinOp)
            and isinstance(operand.op, ast.Sub)
            and _is_shift(operand.left, "close", shift, variables)
            and _is_rolling_mean(operand.right, "close", window, variables)
        )
    return False


def _is_method_call(
    node: ast.AST,
    field_name: str,
    operation: str,
    period: int,
    variables: Mapping[str, int],
) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.casefold() == operation.casefold()
        and _is_name(node.func.value, field_name)
    ):
        return False
    period_node: ast.AST | None = node.args[0] if node.args else None
    if period_node is None:
        period_node = next(
            (
                keyword.value
                for keyword in node.keywords
                if (
                    keyword.arg is not None
                    and keyword.arg.casefold() in {"period", "periods", "window"}
                )
            ),
            None,
        )
    return period_node is not None and _node_static_int(period_node, variables) == period


def _is_correct_fixed_horizon(
    expression: str,
    family: str,
    period: int,
    declared_window: int,
) -> bool:
    body = _formula_expression_node(expression)
    if body is None:
        return False
    variables = {"window": declared_window}
    if family == "long_short_pct":
        return _is_method_call(body, "long_short_ratio", "pct_change", period, variables)
    return (
        isinstance(body, ast.Call)
        and isinstance(body.func, ast.Attribute)
        and body.func.attr.casefold() == "diff"
        and _method_call_period(body, variables) == period
        and _is_method_call(body.func.value, "funding_rate", "diff", period, variables)
    )


def _method_call_period(node: ast.Call, variables: Mapping[str, int]) -> int | None:
    period_node: ast.AST | None = node.args[0] if node.args else None
    if period_node is None:
        period_node = next(
            (
                keyword.value
                for keyword in node.keywords
                if (
                    keyword.arg is not None
                    and keyword.arg.casefold() in {"period", "periods", "window"}
                )
            ),
            None,
        )
    return _node_static_int(period_node, variables) if period_node is not None else None


def _is_iv_rv_difference(expression: str) -> bool:
    body = _formula_expression_node(expression)
    if body is None:
        return False
    return (
        isinstance(body, ast.BinOp)
        and isinstance(body.op, ast.Sub)
        and _is_name(body.left, "ATM_IV")
        and _is_name(body.right, "realized_vol")
    )


def _collect_required_field_sets(
    factor_ref: str,
    details: Sequence[FactorDetail],
    evidence: Sequence[FormulaEvidence],
    mcp_detail: Mapping[str, Any] | None,
    formula_cache: Mapping[tuple[Any, ...], Mapping[str, Any]],
) -> list[tuple[str, set[str]]]:
    del factor_ref
    results: list[tuple[str, set[str]]] = []
    selected_detail = _latest_detail(details)
    if selected_detail is not None:
        results.append(("current.db_detail", _definition_schema_fields(selected_detail, None)))
    for index, formula in enumerate(evidence):
        results.append((f"batch.db_formula_evidence[{index}]", {str(value) for value in formula.required_fields}))
        returned = formula_cache.get(_formula_key(formula))
        values = returned.get("required_fields") if returned else None
        if isinstance(values, list):
            results.append((f"batch.mcp_formula[{index}]", {str(value) for value in values}))
    if mcp_detail is not None:
        results.append(("current.mcp_detail", _definition_schema_fields(None, mcp_detail)))
    return results


def _full_metric_identity(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    identity = dict(value)
    identity.pop("evaluation_type", None)
    identity.pop("ic_scope", None)
    if not identity:
        return None
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)


def _metric_pair_identity(metric: EvaluationMetric) -> tuple[str, str] | None:
    pair_hash = metric.metric_pair_identity_hash
    if isinstance(pair_hash, str) and pair_hash.strip():
        return "repository_hash", pair_hash.strip()
    full_identity = _full_metric_identity(metric.metric_identity)
    if full_identity is not None:
        return "full_identity", full_identity
    return None


def _metric_pair_key(metric: EvaluationMetric) -> tuple[Any, ...]:
    return (
        metric.eval_batch_id,
        metric.factor_ref,
        metric.factor_type,
        metric.factor_id,
        metric.factor_version,
        metric.market_scope,
        metric.label_kind,
        metric.label_code,
        metric.interval,
        metric.return_bar_interval,
        metric.forward_return_bars,
        metric.window_scope,
        metric.sample_start_date,
        metric.sample_end_date,
        _metric_pair_identity(metric) or ("missing_identity", ""),
    )


def _metric_pairs(
    metrics: Sequence[EvaluationMetric],
) -> tuple[dict[tuple[Any, ...], dict[str, EvaluationMetric]], list[CalculationIssue]]:
    pairs: dict[tuple[Any, ...], dict[str, EvaluationMetric]] = defaultdict(dict)
    issues: list[CalculationIssue] = []
    for metric in metrics:
        if metric.evaluation_type not in _SCOPES:
            issues.append(
                _failed(
                    "METRIC_EVALUATION_TYPE_UNKNOWN",
                    "metric 包含未知 evaluation_type。",
                    metric.factor_ref,
                    metric_id=metric.id,
                    evaluation_type=metric.evaluation_type,
                )
            )
            continue
        if _metric_pair_identity(metric) is None:
            issues.append(
                _blocked(
                    "METRIC_IDENTITY_MISSING",
                    "metric 同时缺少 metric_pair_identity_hash 和完整 metric_identity，不能可靠配对 TS/CS。",
                    metric.factor_ref,
                    metric_id=metric.id,
                )
            )
        key = _metric_pair_key(metric)
        if metric.evaluation_type in pairs[key]:
            issues.append(
                _blocked(
                    "METRIC_SCOPE_NOT_UNIQUE",
                    "同一完整 metric identity 的同一 scope 命中多行。",
                    metric.factor_ref,
                    metric_id=metric.id,
                )
            )
            continue
        pairs[key][metric.evaluation_type] = metric
    return dict(pairs), issues


def _metric_is_valid(metric: EvaluationMetric) -> bool:
    return metric.metric_status == "success" and metric.is_valid is True


def _configured_route_profile_weights(
    batch: PublishedEvaluationBatch,
) -> tuple[dict[str, Decimal] | None, str | None, str | None]:
    """Read explicit route weights from a batch evaluation-config snapshot.

    The route evidence is deliberately not a configuration source.  A batch
    may encode the profile as a direct ``profile_weights`` object, under
    ``configured_profile_weights``, or under an explicitly named
    ``route_profiles``/``profiles`` entry.  These are all configuration paths
    in the immutable batch JSON; no client default is used.  The return tuple
    is ``(weights, path, error)``.  ``weights`` is ``None`` when the snapshot
    does not contain a complete, finite, non-negative TS/CS mapping.
    """

    config = batch.evaluation_config
    if not isinstance(config, Mapping):
        return None, None, "evaluation_config is not an object"

    profile = batch.route_profile_key
    candidates: list[tuple[str, Any]] = []

    # A profile-specific object is more authoritative than a batch-wide alias.
    for container_name in ("route_profiles", "profiles"):
        container = config.get(container_name)
        if not isinstance(container, Mapping) or profile not in container:
            continue
        profile_config = container.get(profile)
        if not isinstance(profile_config, Mapping):
            candidates.append((f"{container_name}.{profile}", profile_config))
            continue
        for key in ("configured_profile_weights", "profile_weights", "weights"):
            if key in profile_config:
                candidates.append((f"{container_name}.{profile}.{key}", profile_config.get(key)))

    for key in ("configured_profile_weights", "profile_weights", "route_profile_weights"):
        if key in config:
            candidates.append((key, config.get(key)))

    routing = config.get("routing")
    if isinstance(routing, Mapping):
        for key in ("configured_profile_weights", "profile_weights", "weights"):
            if key in routing:
                candidates.append((f"routing.{key}", routing.get(key)))

    if not candidates:
        return None, None, "no explicit profile weight mapping in evaluation_config"

    parsed_candidates: list[tuple[str, dict[str, Decimal]]] = []
    invalid_candidates: list[tuple[str, str]] = []
    for path, raw in candidates:
        parsed, error = _parse_route_profile_weights(raw)
        if parsed is None:
            invalid_candidates.append((path, error or "invalid profile weight mapping"))
        else:
            parsed_candidates.append((path, parsed))

    # Every supplied alias is part of the immutable batch configuration.  A
    # malformed secondary alias must not be silently ignored just because the
    # first alias happens to be valid.
    if invalid_candidates:
        paths = ";".join(path for path, _ in invalid_candidates)
        details = "; ".join(f"{path}: {error}" for path, error in invalid_candidates)
        return None, paths, f"invalid profile weight mappings ({details})"
    if not parsed_candidates:
        return None, None, "no valid profile weight mapping in evaluation_config"

    first_path, first_weights = parsed_candidates[0]
    conflicting = [
        (path, weights)
        for path, weights in parsed_candidates[1:]
        if weights != first_weights
    ]
    if conflicting:
        paths = ";".join([first_path, *(path for path, _ in conflicting)])
        return (
            None,
            paths,
            "conflicting profile weight mappings: "
            f"{first_path}={first_weights!r}; "
            + "; ".join(f"{path}={weights!r}" for path, weights in conflicting),
        )
    return first_weights, first_path, None


def _parse_route_profile_weights(
    raw: Any,
) -> tuple[dict[str, Decimal] | None, str | None]:
    """Parse one explicit TS/CS route-weight mapping.

    The parser intentionally does not normalize or infer missing dimensions:
    both scopes must be finite, non-negative decimals and their total must be
    positive.  This lets the caller compare every configured alias before
    selecting a source, preventing silent precedence when snapshots disagree.
    """

    if not isinstance(raw, Mapping):
        return None, "profile weight mapping is not an object"
    parsed = {scope: _to_decimal(raw.get(scope)) for scope in _SCOPES}
    if any(value is None for value in parsed.values()):
        return None, "profile weight mapping must contain finite TS and CS values"
    if any(value < 0 for value in parsed.values() if value is not None):
        return None, "profile weight mapping cannot contain negative values"
    if sum(parsed.values(), Decimal(0)) <= 0:  # type: ignore[arg-type]
        return None, "profile weight mapping must have a positive sum"
    return (
        {scope: parsed[scope] for scope in _SCOPES if parsed[scope] is not None},
        None,
    )


def _as_string_set(value: Any) -> set[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return set(value)


def _check_any_valid_eligibility(
    metric: EvaluationMetric,
    eligibility: Mapping[str, Any],
    valid_scopes: Sequence[str],
    invalid_scopes: Sequence[str],
    issues: list[CalculationIssue],
) -> None:
    if eligibility.get("admission_mode") != "any_valid_scope":
        issues.append(
            _failed(
                "VALIDITY_ADMISSION_MODE_MISMATCH",
                "route_eligibility.admission_mode 不是 any_valid_scope。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
    actual_valid = _as_string_set(eligibility.get("valid_scopes"))
    actual_invalid = _as_string_set(eligibility.get("invalid_scopes"))
    if actual_valid != set(valid_scopes) or actual_invalid != set(invalid_scopes):
        issues.append(
            _failed(
                "VALIDITY_SCOPE_CLASSIFICATION_MISMATCH",
                "route_eligibility 的 valid_scopes/invalid_scopes 与 metric 真值不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                expected_valid=list(valid_scopes),
                expected_invalid=list(invalid_scopes),
            )
        )
    actual_eligible = eligibility.get("is_eligible")
    if not isinstance(actual_eligible, bool):
        issues.append(
            _blocked(
                "VALIDITY_ELIGIBLE_FLAG_MISSING",
                "route_eligibility.is_eligible 缺失或不是布尔值。",
                metric.factor_ref,
                metric_id=metric.id,
            )
        )
        return
    if not valid_scopes:
        expected_eligible = False
    else:
        score = _to_decimal(eligibility.get("routing_score"))
        minimum = _to_decimal(eligibility.get("minimum_route_score"))
        if score is None or minimum is None:
            issues.append(
                _blocked(
                    "VALIDITY_ROUTE_THRESHOLD_EVIDENCE_MISSING",
                    "存在有效 scope，但缺少 routing_score/minimum_route_score，不能裁决其它准入门槛。",
                    metric.factor_ref,
                    metric_id=metric.id,
                )
            )
            return
        expected_eligible = score >= minimum
    if actual_eligible is not expected_eligible:
        issues.append(
            _failed(
                "VALIDITY_ANY_SCOPE_RESULT_MISMATCH",
                "TS/CS 任一维有效并结合 route 分数门槛后的 is_eligible 结果不一致。",
                metric.factor_ref,
                metric_id=metric.id,
                expected=expected_eligible,
                actual=actual_eligible,
            )
        )


def _plain_int(value: Any) -> int | None:
    """Parse an integer without silently truncating fractional values.

    JSON identity fields are commonly returned as either integers or decimal
    strings.  ``int(1.9)`` would silently turn malformed input into ``1`` and
    could make a route point at a different metric, so fractional numbers and
    non-integer strings are rejected instead of coerced.
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            return int(value)
        return None
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal.is_finite() else None


def _matches_persisted_scale(calculated: Decimal, persisted: Decimal) -> bool:
    """Return whether ``calculated`` equals ``persisted`` at its stored scale.

    ``Decimal.quantize`` can raise ``InvalidOperation`` or ``Overflow`` for a
    malformed/unbounded exponent even when the input value itself is finite.
    Persisted values originate in database/API JSON and are therefore not
    trusted merely because they parsed as ``Decimal``.  Treat an unquantizable
    scale as a non-match so callers report a structured discrepancy instead of
    crashing the whole calculation audit.
    """

    try:
        quantum = Decimal(1).scaleb(persisted.as_tuple().exponent)
        return calculated.quantize(quantum, rounding=ROUND_HALF_UP) == persisted
    except (DecimalException, OverflowError, ValueError):
        return False


def _same_route_metric_identity(route: PublishedRoute, metric: EvaluationMetric) -> bool:
    return (
        route.eval_batch_id == metric.eval_batch_id
        and route.factor_ref == metric.factor_ref
        and route.factor_type == metric.factor_type
        and route.factor_id == metric.factor_id
        and route.factor_version == metric.factor_version
        and route.market_scope == metric.market_scope
        and route.label_kind == metric.label_kind
        and route.label_code == metric.label_code
    )


def _check_route_identity(
    snapshot: CalculationAuditSnapshot,
    route: PublishedRoute,
    issues: list[CalculationIssue],
) -> None:
    del snapshot
    if route.factor_ref != f"{route.factor_type}:{route.factor_id}":
        issues.append(
            _failed(
                "ROUTE_PUBLICATION_IDENTITY_MISMATCH",
                "route.factor_ref 与 factor_type/factor_id 身份不一致。",
                route.factor_ref,
                route_id=route.id,
                field="factor_ref",
                expected=f"{route.factor_type}:{route.factor_id}",
                actual=route.factor_ref,
            )
        )


def _check_route_snapshot_fields(
    snapshot: CalculationAuditSnapshot,
    route: PublishedRoute,
    issues: list[CalculationIssue],
) -> None:
    """Reconcile route publication identity and exact snapshot timestamps.

    ``route.evidence`` is an output and is deliberately excluded as an
    authority.  We check the immutable batch/publication selectors here; the
    caller independently reconstructs metric pairs and score columns.  The
    ``environment_date`` is checked for a valid calendar date.  Equality with
    ``batch.end_date`` is asserted only when the batch explicitly declares the
    route field to mean ``publication_effective``.  Snapshot-member semantics
    are reconciled by CALC-513, and an undefined meaning is never guessed.
    No rejection-code vocabulary is inferred for ineligible routes.
    """

    batch = snapshot.batch
    identity_fields: tuple[tuple[str, Any, Any], ...] = (
        ("eval_batch_id", route.eval_batch_id, batch.id),
        ("publication_uid", route.publication_uid, batch.publication_uid),
        ("publish_version", route.publish_version, batch.publish_version),
        ("market_scope", route.market_scope, batch.market_scope),
        ("route_profile_key", route.route_profile_key, batch.route_profile_key),
        ("label_kind", route.label_kind, batch.label_kind),
        ("score_rule_version", route.score_rule_version, batch.score_rule_version),
    )
    mismatches = {
        field_name: {"expected": expected, "actual": actual}
        for field_name, actual, expected in identity_fields
        if actual != expected
    }
    if mismatches:
        issues.append(
            _failed(
                "ROUTE_PUBLICATION_IDENTITY_MISMATCH",
                "route 与 batch 的 publication/profile/批次/score-rule 身份不一致。",
                route.factor_ref,
                route_id=route.id,
                mismatches=mismatches,
            )
        )

    expected_as_of_time = batch.as_of_time
    actual_as_of_time = route.as_of_time
    if not isinstance(expected_as_of_time, datetime):
        issues.append(
            _blocked(
                "ROUTE_BATCH_AS_OF_TIME_MISSING",
                "batch 缺少有效 as_of_time，不能核对 route 快照身份。",
                route.factor_ref,
                route_id=route.id,
            )
        )
    elif not isinstance(actual_as_of_time, datetime):
        issues.append(
            _blocked(
                "ROUTE_AS_OF_TIME_MISSING",
                "route 缺少有效 as_of_time，不能核对计算快照。",
                route.factor_ref,
                route_id=route.id,
            )
        )
    elif actual_as_of_time != expected_as_of_time:
        issues.append(
            _failed(
                "ROUTE_AS_OF_TIME_MISMATCH",
                "route.as_of_time 与 batch.as_of_time 不一致。",
                route.factor_ref,
                route_id=route.id,
                expected_as_of_time=expected_as_of_time.isoformat(),
                actual_as_of_time=actual_as_of_time.isoformat(),
            )
        )

    actual_environment_date = route.environment_date
    if not isinstance(actual_environment_date, date) or isinstance(actual_environment_date, datetime):
        issues.append(
            _blocked(
                "ROUTE_ENVIRONMENT_DATE_MISSING",
                "route 缺少有效 environment_date。",
                route.factor_ref,
                route_id=route.id,
            )
        )
    elif _route_environment_date_semantics(
        getattr(batch, "environment_snapshot", None)
    ) == "publication_effective":
        expected_environment_date = batch.end_date
        if not isinstance(expected_environment_date, date) or isinstance(
            expected_environment_date, datetime
        ):
            issues.append(
                _blocked(
                    "ROUTE_BATCH_END_DATE_MISSING",
                    "batch 缺少有效 end_date，不能核对 publication_effective route 日期。",
                    route.factor_ref,
                    route_id=route.id,
                )
            )
        elif actual_environment_date != expected_environment_date:
            issues.append(
                _failed(
                    "ROUTE_ENVIRONMENT_DATE_MISMATCH",
                    "明确声明 publication_effective 语义时，route.environment_date 必须等于 batch.end_date。",
                    route.factor_ref,
                    route_id=route.id,
                    expected_environment_date=expected_environment_date.isoformat(),
                    actual_environment_date=actual_environment_date.isoformat(),
                )
            )


def _compare_scope_evidence_decimal(
    expected: Decimal,
    evidence: Mapping[str, Any],
    field_name: str,
    issues: list[CalculationIssue],
    code: str,
    message: str,
    route: PublishedRoute,
    scope: str,
) -> None:
    """Compare a required Decimal in one valid scope's route evidence.

    A valid scope must carry an explicit, finite Decimal value.  Missing or
    malformed values are data-precondition blocks; only an actual numeric
    disagreement is a calculation failure.  This prevents an omitted nested
    evidence field from being silently treated as an optional value.
    """

    if field_name not in evidence or evidence.get(field_name) is None:
        issues.append(
            _blocked(
                f"{code}_MISSING",
                "route evidence 的有效 scope 缺少必要的 Decimal 字段。",
                route.factor_ref,
                route_id=route.id,
                scope=scope,
                field=field_name,
            )
        )
        return
    stored = _to_decimal(evidence.get(field_name))
    if stored is None:
        issues.append(
            _blocked(
                f"{code}_INVALID",
                "route evidence 的有效 scope Decimal 字段不可解析。",
                route.factor_ref,
                route_id=route.id,
                scope=scope,
                field=field_name,
            )
        )
    elif not _matches_persisted_scale(expected, stored):
        issues.append(
            _failed(
                code,
                message,
                route.factor_ref,
                route_id=route.id,
                scope=scope,
                expected=str(expected),
                stored=str(stored),
            )
        )


def _compare_route_scope_score(
    expected: Decimal | None,
    actual: Any,
    issues: list[CalculationIssue],
    route: PublishedRoute,
    scope: str,
) -> None:
    """Compare a persisted route TS/CS score, including required nulls.

    A score is meaningful only for a successful, valid metric in that scope.
    Therefore an invalid/not-applicable scope must be persisted as ``NULL``;
    silently ignoring a non-null value would allow stale scores from another
    evaluation to survive a route rebuild.
    """

    stored = _to_decimal(actual)
    if expected is None:
        if actual is None:
            return
        if stored is None:
            issues.append(
                _blocked(
                    "ROUTE_SCOPE_SCORE_INVALID",
                    "route 的 TS/CS score 不是可解析 Decimal 或 NULL。",
                    route.factor_ref,
                    route_id=route.id,
                    scope=scope,
                    actual=actual,
                )
            )
        else:
            issues.append(
                _failed(
                    "ROUTE_SCOPE_SCORE_MUST_BE_NULL",
                    "无效或不适用的 metric scope 在 route 上必须为 NULL。",
                    route.factor_ref,
                    route_id=route.id,
                    scope=scope,
                    expected=None,
                    actual=str(stored),
                )
            )
        return
    if stored is None:
        issues.append(
            _blocked(
                "ROUTE_SCOPE_SCORE_MISSING",
                "有效 metric scope 缺少 route TS/CS score。",
                route.factor_ref,
                route_id=route.id,
                scope=scope,
                expected=str(expected),
            )
        )
    elif not _matches_persisted_scale(expected, stored):
        issues.append(
            _failed(
                "ROUTE_SCOPE_SCORE_MISMATCH",
                "route TS/CS score 与同一 batch 中独立恢复的 metric score 不一致。",
                route.factor_ref,
                route_id=route.id,
                scope=scope,
                expected=str(expected),
                actual=str(stored),
            )
        )


def _check_route_admission_fields(
    route: PublishedRoute,
    *,
    valid_scopes: Sequence[str],
    independently_calculated_score: Decimal | None,
    minimum_route_score: Decimal | None,
    issues: list[CalculationIssue],
) -> None:
    """Check route eligibility against the independently calculated score.

    The route contract defines admission as ``any_valid_scope`` plus a score
    threshold.  We only assert the rejection-reason *absence* for an eligible
    route; the finite set and meaning of rejection codes for ineligible rows
    are not specified by the current Factor 4.0 contract and must not be
    guessed by this oracle.
    """

    if independently_calculated_score is None or minimum_route_score is None:
        return
    # env-score-v1 section 8.4 emits a six-place routing_score before the
    # batch executor applies its minimum; compare the same final value here.
    final_score = independently_calculated_score.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    floor_score = independently_calculated_score.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if independently_calculated_score - floor_score == Decimal("0.0000005") and (floor_score >= minimum_route_score) != (final_score >= minimum_route_score):
        issues.append(_blocked_doc("ROUTE_ADMISSION_ROUNDING_MIDPOINT_UNSPECIFIED", "精确舍入中点影响最低分准入，缺少对应舍入策略。", route.factor_ref, route_id=route.id))
        return
    expected_eligible = bool(valid_scopes) and final_score >= minimum_route_score
    if route.is_eligible is not expected_eligible:
        issues.append(
            _failed(
                "ROUTE_ELIGIBILITY_MISMATCH",
                "route.is_eligible 与独立重算的有效 scope/最低分准入结果不一致。",
                route.factor_ref,
                route_id=route.id,
                expected=expected_eligible,
                actual=route.is_eligible,
                calculated_score=str(independently_calculated_score),
                final_score=str(final_score),
                minimum_route_score=str(minimum_route_score),
                valid_scopes=list(valid_scopes),
            )
        )
    if route.is_eligible and route.reject_reason_code is not None:
        issues.append(
            _failed(
                "ROUTE_REJECT_REASON_ON_ELIGIBLE",
                "eligible route 不应携带 reject_reason_code。",
                route.factor_ref,
                route_id=route.id,
                reject_reason_code=route.reject_reason_code,
            )
        )


def _compare_required_decimal(
    expected: Decimal,
    value: Any,
    issues: list[CalculationIssue],
    code: str,
    route: PublishedRoute,
) -> None:
    stored = _to_decimal(value)
    if stored is None:
        issues.append(
            _blocked(
                f"{code}_MISSING",
                "route evidence 缺少必要的 Decimal 中间值。",
                route.factor_ref,
                route_id=route.id,
            )
        )
    elif not _matches_persisted_scale(expected, stored):
        issues.append(
            _failed(
                code,
                "route evidence 的 Decimal 中间值与独立重算不一致。",
                route.factor_ref,
                route_id=route.id,
                expected=str(expected),
                stored=str(stored),
            )
        )


def _check_metric_direction(
    metric: EvaluationMetric,
    issues: list[CalculationIssue],
    route_id: int,
) -> None:
    direction = metric.direction
    predictive = _plain_int(direction.get("predictive_direction")) if isinstance(direction, Mapping) else None
    if predictive not in {-1, 1}:
        issues.append(
            _blocked(
                "METRIC_PREDICTIVE_DIRECTION_MISSING",
                "有效 metric 缺少 -1/1 predictive_direction。",
                metric.factor_ref,
                route_id=route_id,
                metric_id=metric.id,
            )
        )
        return
    directed_sources: list[Mapping[str, Any]] = []
    if isinstance(metric.metric_payload, Mapping):
        directed_sources.append(metric.metric_payload)
    if isinstance(direction, Mapping):
        directed_sources.append(direction)
    for raw_name, directed_name in (
        ("mean_ic", "directed_mean_ic"),
        ("mean_rank_ic", "directed_mean_rank_ic"),
        ("icir", "directed_icir"),
        ("rank_icir", "directed_rank_icir"),
    ):
        raw = _to_decimal(getattr(metric, raw_name))
        directed_values: list[Decimal] = []
        invalid_source_count = 0
        for source in directed_sources:
            if directed_name not in source or source.get(directed_name) is None:
                continue
            parsed = _to_decimal(source.get(directed_name))
            if parsed is None:
                invalid_source_count += 1
            else:
                directed_values.append(parsed)
        if raw is None or invalid_source_count or not directed_values:
            issues.append(
                _blocked(
                    "METRIC_DIRECTED_VALUE_MISSING",
                    "有效 metric 缺少原始或 directed IC/ICIR 值。",
                    metric.factor_ref,
                    route_id=route_id,
                    metric_id=metric.id,
                    field=directed_name,
                )
            )
        elif len(set(directed_values)) > 1:
            issues.append(
                _failed(
                    "METRIC_DIRECTED_VALUE_CONFLICT",
                    "metric 的不同 directed 值来源彼此不一致，不能选择其中一份作为真值。",
                    metric.factor_ref,
                    route_id=route_id,
                    metric_id=metric.id,
                    field=directed_name,
                    values=[str(value) for value in directed_values[:_ISSUE_SAMPLE_LIMIT]],
                )
            )
        elif not _matches_persisted_scale(raw * Decimal(predictive), directed_values[0]):
            issues.append(
                _failed(
                    "METRIC_DIRECTED_VALUE_MISMATCH",
                    "directed IC/ICIR 不等于原始值乘 predictive_direction。",
                    metric.factor_ref,
                    route_id=route_id,
                    metric_id=metric.id,
                    field=directed_name,
                    expected=str(raw * Decimal(predictive)),
                    stored=str(directed_values[0]),
                )
            )


def _partition_key(route: PublishedRoute) -> tuple[Any, ...]:
    """Return the complete ranking partition key using the documented date.

    ``as_of_time`` remains an exact identity field and is checked separately
    against the batch.  It must not be used as a partition key: two rows from
    the same ``as_of_date`` with different clock precision belong to one
    ranking and otherwise can hide duplicate or missing ranks.
    """

    return (
        route.eval_batch_id,
        route.publication_uid,
        route.publish_version,
        route.market_scope,
        route.route_profile_key,
        route.label_kind,
        route.label_code,
        route.environment_date,
        _as_of_date(route.as_of_time),
    )


def _as_of_date(value: Any) -> date | None:
    """Normalize a route/batch timestamp to the ranking's calendar date."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _route_partitions(
    routes: Sequence[PublishedRoute],
) -> dict[tuple[Any, ...], list[PublishedRoute | RouteRankingEntry]]:
    partitions: dict[tuple[Any, ...], list[PublishedRoute | RouteRankingEntry]] = defaultdict(list)
    for route in routes:
        if route.is_active and route.is_eligible:
            partitions[_partition_key(route)].append(route)
    for rows in partitions.values():
        rows.sort(key=lambda row: row.rank_no)
    return dict(partitions)


def _repeat_route_partitions(
    snapshot: PublishedRouteSnapshot | CalculationAuditSnapshot,
) -> dict[tuple[Any, ...], list[PublishedRoute | RouteRankingEntry]]:
    if isinstance(snapshot, CalculationAuditSnapshot):
        return _route_partitions(snapshot.routes)
    partitions: dict[tuple[Any, ...], list[PublishedRoute | RouteRankingEntry]] = defaultdict(list)
    for route in snapshot.routes:
        key = (
            snapshot.batch_id,
            snapshot.publication_uid,
            snapshot.publish_version,
            snapshot.market_scope,
            snapshot.route_profile_key,
            route.label_kind,
            route.label_code,
            route.environment_date,
            _as_of_date(route.as_of_time),
        )
        partitions[key].append(route)
    for rows in partitions.values():
        rows.sort(key=lambda row: row.rank_no)
    return dict(partitions)


def _check_route_as_of_contract(
    expected_as_of_time: Any,
    routes: Sequence[PublishedRoute | RouteRankingEntry],
    issues: list[CalculationIssue],
    read_name: str,
) -> None:
    """Verify every route carries the exact batch snapshot timestamp.

    Ranking uses the calendar ``as_of_date`` for partitioning, but route rows
    still must point to the exact batch ``as_of_time``.  This two-level check
    catches same-day timestamp drift instead of allowing it to form separate
    one-row partitions.  Invalid timestamps are data-precondition blocks;
    explicit timestamp disagreement is a calculation failure.
    """

    if not isinstance(expected_as_of_time, datetime):
        issues.append(
            _blocked(
                "RANK_BATCH_AS_OF_TIME_MISSING",
                "batch 缺少有效 as_of_time，不能核对 route 快照身份。",
                read=read_name,
            )
        )
        return
    expected_date = expected_as_of_time.date()
    for route in routes:
        actual_as_of_time = route.as_of_time
        if not isinstance(actual_as_of_time, datetime):
            issues.append(
                _blocked(
                    "RANK_ROUTE_AS_OF_TIME_MISSING",
                    "route 缺少有效 as_of_time，不能确定排名快照日期。",
                    route.factor_ref,
                    route_id=route.id,
                    read=read_name,
                )
            )
            continue
        if actual_as_of_time != expected_as_of_time:
            issues.append(
                _failed(
                    "RANK_ROUTE_AS_OF_TIME_MISMATCH",
                    "route.as_of_time 与 batch.as_of_time 不一致。",
                    route.factor_ref,
                    route_id=route.id,
                    read=read_name,
                    expected_as_of_time=expected_as_of_time.isoformat(),
                    actual_as_of_time=actual_as_of_time.isoformat(),
                    expected_as_of_date=expected_date.isoformat(),
                    actual_as_of_date=actual_as_of_time.date().isoformat(),
                )
            )


def _check_partition_ranks(
    partitions: Mapping[tuple[Any, ...], Sequence[PublishedRoute | RouteRankingEntry]],
    issues: list[CalculationIssue],
    read_name: str,
) -> None:
    for partition, routes in partitions.items():
        ranks = [route.rank_no for route in routes]
        if ranks != list(range(1, len(routes) + 1)):
            issues.append(
                _failed(
                    "RANK_NOT_CONTIGUOUS",
                    "排名分区内 rank 未从 1 连续递增。",
                    read=read_name,
                    partition=str(partition),
                    ranks=ranks,
                )
            )
        if any(left.routing_score < right.routing_score for left, right in zip(routes, routes[1:])):
            issues.append(
                _failed(
                    "RANK_SCORE_NOT_DESCENDING",
                    "排名分区未按 Decimal routing_score 降序排列。",
                    read=read_name,
                    partition=str(partition),
                )
            )
        identities = [(route.factor_ref, route.factor_version) for route in routes]
        if len(identities) != len(set(identities)):
            issues.append(
                _failed(
                    "RANK_FACTOR_VERSION_DUPLICATE",
                    "同一排名分区内 factor/version 重复。",
                    read=read_name,
                    partition=str(partition),
                )
            )


def _publication_identity(
    snapshot: PublishedRouteSnapshot | CalculationAuditSnapshot,
) -> tuple[Any, ...]:
    if isinstance(snapshot, PublishedRouteSnapshot):
        return (
            snapshot.batch_id,
            snapshot.publication_uid,
            snapshot.publish_version,
            snapshot.market_scope,
            snapshot.route_profile_key,
        )
    batch = snapshot.batch
    return (
        batch.id,
        batch.publication_uid,
        batch.publish_version,
        batch.market_scope,
        batch.route_profile_key,
    )


def _partition_sequences(
    partitions: Mapping[tuple[Any, ...], Sequence[PublishedRoute | RouteRankingEntry]],
) -> dict[tuple[Any, ...], tuple[tuple[Any, ...], ...]]:
    return {
        partition: tuple(
            (
                route.id,
                route.rank_no,
                route.factor_ref,
                route.factor_version,
                route.routing_score,
                route.metric_id,
            )
            for route in routes
        )
        for partition, routes in partitions.items()
    }
