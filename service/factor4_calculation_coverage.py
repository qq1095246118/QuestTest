"""Factor 4.0 calculation coverage manifest and precondition gate.

The R0 service can prove five calculation checks from the currently available
published snapshot.  The remaining calculation sub-cases need raw rows or a
controlled fixture.  This module keeps those sub-cases explicit so an
executor cannot silently omit them or turn a fixture-independent structural
check into a product PASS.  When only final metric/route output is available,
use ``tests/cases/factor4/test_final_results.py``; it is a result-consistency
audit and does not claim independent formula recalculation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from service.factor4_calculation_service import CalculationCheckResult, CalculationIssue


CoverageReadiness = Literal["READY", "BLOCKED_DATA_PRECONDITION"]
CoverageMode = Literal["READ_ONLY", "R1_WRITE"]


@dataclass(frozen=True)
class Factor4CoverageCase:
    """Describe one calculation sub-case that is not yet executable from R0 data.

    ``required_preconditions`` contains named, independently checkable data or
    fixture capabilities.  The names are deliberately opaque to the test
    runner's business logic; a fixture provider supplies a boolean for each
    name and this service only applies the fail-closed gate.
    """

    case_id: str
    module: str
    title: str
    mode: CoverageMode
    oracle: str
    required_preconditions: tuple[str, ...]


@dataclass(frozen=True)
class Factor4CoverageAssessment:
    """Hold readiness for one pending calculation sub-case.

    ``READY`` means all declared inputs are available, not that the product
    calculation passed.  A live evaluator must still execute the case and
    provide independent evidence before a PASS can be recorded.
    """

    case: Factor4CoverageCase
    status: CoverageReadiness
    missing_preconditions: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return whether every declared fixture/data precondition is present."""

        return self.status == "READY"

    @property
    def missing_reason_codes(self) -> tuple[str, ...]:
        """Return stable report codes for each missing data/fixture precondition."""

        return tuple(_missing_reason_code(name) for name in self.missing_preconditions)


# Historical raw-data/controlled-fixture plan, not the default result-acceptance
# backlog. Keep its identities and order for traceability; scope selection now
# lives on the business Cases, independently of this precondition inventory.
PENDING_FACTOR4_CALCULATION_CASES: tuple[Factor4CoverageCase, ...] = (
    Factor4CoverageCase(
        "ENV-104-A",
        "factor4.environment_revision",
        "revision/PIT 三点边界",
        "R1_WRITE",
        "revision_pit_selection",
        ("MULTI_REVISION_SAMPLE", "PIT_BOUNDARY_RULE"),
    ),
    Factor4CoverageCase(
        "LIFE-405-A",
        "factor4.batch_snapshot",
        "计算输入快照冻结",
        "R1_WRITE",
        "snapshot_immutability",
        ("FROZEN_BATCH_SNAPSHOT", "RELATION_VERSION_FIXTURE"),
    ),
    Factor4CoverageCase(
        "CALC-501-A",
        "factor4.time_series",
        "time-series 独立数值重算",
        "READ_ONLY",
        "time_series_metric",
        (
            "RAW_FACTOR_VALUES",
            "RAW_FORWARD_RETURNS",
            "TS_METRIC_RULE",
            "TS_METRIC_EVIDENCE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-501-B",
        "factor4.cross_sectional",
        "cross-sectional 独立数值重算",
        "READ_ONLY",
        "cross_sectional_metric",
        (
            "MULTI_ASSET_TIME_MATRIX",
            "RAW_FORWARD_RETURNS",
            "CS_METRIC_RULE",
            "CS_METRIC_EVIDENCE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-502-A",
        "factor4.time_alignment",
        "时间轴、频率与 forward-return 对齐",
        "READ_ONLY",
        "time_forward_alignment",
        (
            "RAW_BAR_TIMESTAMPS",
            "FREQUENCY_HORIZON_RULE",
            "FORWARD_RETURN_BOUNDARY_RULE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-502-B",
        "factor4.gap_policy",
        "缺口与连续区间策略",
        "R1_WRITE",
        "gap_segment_policy",
        ("GAP_FIXTURE", "ENVIRONMENT_MASK", "GAP_POLICY_RULE"),
    ),
    Factor4CoverageCase(
        "CALC-503-A",
        "factor4.oos_fold",
        "OOS/fold 独立聚合",
        "READ_ONLY",
        "fold_aggregation",
        (
            "RAW_SAMPLE_ROWS",
            "FOLD_BOUNDARIES",
            "FOLD_AGGREGATION_RULE",
            "BATCH_AS_OF_TIME",
        ),
    ),
    Factor4CoverageCase(
        "CALC-503-B",
        "factor4.future_perturbation",
        "未来数据扰动不变性",
        "R1_WRITE",
        "future_data_invariance",
        (
            "FUTURE_PERTURBATION_PAIR",
            "FIXED_AS_OF_SNAPSHOT",
            "SECOND_ISOLATED_RUN",
        ),
    ),
    Factor4CoverageCase(
        "CALC-505-A",
        "factor4.validity_threshold",
        "Validity 门槛边界",
        "R1_WRITE",
        "threshold_boundary",
        ("THRESHOLD_BOUNDARY_FIXTURE", "VALIDITY_THRESHOLD_RULE"),
    ),
    Factor4CoverageCase(
        "CALC-507-B",
        "factor4.partition_isolation",
        "跨分区隔离",
        "R1_WRITE",
        "partition_isolation",
        ("MULTI_PARTITION_FIXTURE", "PARTITION_KEY_CONTRACT"),
    ),
    Factor4CoverageCase(
        "CALC-508-A",
        "factor4.zero_cost",
        "零成本恒等关系",
        "READ_ONLY",
        "zero_cost_identity",
        ("GROSS_NET_RETURN_ROWS", "POSITION_TURNOVER_ROWS", "ZERO_COST_CONFIG"),
    ),
    Factor4CoverageCase(
        "CALC-508-B",
        "factor4.nonzero_cost",
        "非零成本独立重算",
        "READ_ONLY",
        "nonzero_cost_recalculation",
        (
            "GROSS_NET_RETURN_ROWS",
            "POSITION_TURNOVER_ROWS",
            "NONZERO_COST_CONFIG",
            "NET_RETURN_EVIDENCE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-510-B",
        "factor4.operator_oracle",
        "公式算子数值 Oracle",
        "READ_ONLY",
        "operator_pointwise_oracle",
        (
            "OPERATOR_ORACLE_FIXTURE",
            "SERVER_POINTWISE_OUTPUT",
            "OPERATOR_SEMANTICS_VERSION",
        ),
    ),
    Factor4CoverageCase(
        "CALC-511-A",
        "factor4.parent_aggregation",
        "母因子聚合独立重算",
        "READ_ONLY",
        "parent_aggregation",
        (
            "PARENT_RELATION_SNAPSHOT",
            "CHILD_POINTWISE_VALUES",
            "AGGREGATION_RULE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-511-B",
        "factor4.parent_relation_version",
        "母子关系版本隔离",
        "R1_WRITE",
        "relation_version_isolation",
        (
            "RELATION_VERSION_FIXTURE",
            "TWO_BATCH_EVIDENCE",
            "CLEANUP_SCOPE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-512-A",
        "factor4.repeatability",
        "真正重复计算一致性",
        "R1_WRITE",
        "independent_repeatability",
        (
            "FROZEN_INPUT_SNAPSHOT",
            "SECOND_INDEPENDENT_RUN",
            "RUN_EXECUTION_EVIDENCE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-512-B",
        "factor4.version_isolation",
        "代码/配置/公式版本隔离",
        "R1_WRITE",
        "version_identity_isolation",
        (
            "VERSION_VARIANTS",
            "HISTORICAL_BATCH_EVIDENCE",
            "ISOLATED_SCOPE",
        ),
    ),
    Factor4CoverageCase(
        "CALC-509",
        "factor4.numeric_precision",
        "数值精度与空值传播",
        "READ_ONLY",
        "numeric_precision_and_null_propagation",
        (
            "NUMERIC_EDGE_FIXTURE",
            "SERVER_NUMERIC_OUTPUT",
            "NUMERIC_POLICY_RULE",
        ),
    ),
)


class Factor4CalculationCoverageService:
    """Expose the pending calculation manifest and fail-closed precondition gate."""

    def __init__(self, cases: Sequence[Factor4CoverageCase] | None = None) -> None:
        """Initialize the coverage service with a validated case manifest.

        ``cases`` may replace the default manifest for a focused unit test or
        a future product variant.  Duplicate IDs, blank IDs and duplicate
        precondition names are rejected with ``ValueError``.  No I/O occurs.
        """

        selected = tuple(cases) if cases is not None else PENDING_FACTOR4_CALCULATION_CASES
        self._validate_cases(selected)
        self._cases = selected

    @property
    def cases(self) -> tuple[Factor4CoverageCase, ...]:
        """Return the immutable ordered coverage manifest."""

        return self._cases

    def manifest(self) -> tuple[dict[str, object], ...]:
        """Return a JSON-safe manifest for an AI test executor.

        Each entry contains the case identity, execution mode, independent
        oracle name, required preconditions and the mandated blocked status
        when those preconditions are absent.  The returned dictionaries are
        newly allocated and may be serialized without exposing credentials.
        """

        return tuple(
            {
                "case_id": case.case_id,
                "module": case.module,
                "title": case.title,
                "mode": case.mode,
                "oracle": case.oracle,
                "preconditions": list(case.required_preconditions),
                "on_missing_precondition": "BLOCKED_DATA_PRECONDITION",
            }
            for case in self._cases
        )

    def assess(
        self,
        availability: Mapping[str, bool] | None = None,
    ) -> tuple[Factor4CoverageAssessment, ...]:
        """Assess all cases against a fixture/data availability map.

        ``availability`` maps manifest precondition names to exact boolean
        values.  Missing or non-``True`` values fail closed.  Unknown names
        raise ``ValueError`` so a misspelled fixture key cannot accidentally
        produce a misleading ready result.  ``READY`` only indicates that an
        independent live evaluator may now run; it never asserts calculation
        correctness.
        """

        values = dict(availability or {})
        known = {
            precondition
            for case in self._cases
            for precondition in case.required_preconditions
        }
        invalid_keys = [key for key in values if not isinstance(key, str)]
        if invalid_keys:
            raise ValueError("Factor 4.0 precondition keys must be strings")
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown Factor 4.0 preconditions: {', '.join(unknown)}")

        assessments: list[Factor4CoverageAssessment] = []
        for case in self._cases:
            missing = tuple(
                precondition
                for precondition in case.required_preconditions
                if values.get(precondition) is not True
            )
            assessments.append(
                Factor4CoverageAssessment(
                    case=case,
                    status="READY" if not missing else "BLOCKED_DATA_PRECONDITION",
                    missing_preconditions=missing,
                )
            )
        return tuple(assessments)

    def build_blocked_checks(
        self,
        availability: Mapping[str, bool] | None = None,
    ) -> tuple[CalculationCheckResult, ...]:
        """Convert readiness assessments into structured non-PASS checks.

        Cases missing data receive a precise ``<case>_PRECONDITION_MISSING``
        finding.  Even when all preconditions are available, the result stays
        ``BLOCKED_DATA_PRECONDITION`` with ``LIVE_ORACLE_EXECUTION_REQUIRED``
        until a real evaluator supplies independent output.  This deliberate
        gate prevents synthetic fixture setup or a manifest check from being
        counted as a Factor 4.0 product PASS.
        """

        checks: list[CalculationCheckResult] = []
        for assessment in self.assess(availability):
            case = assessment.case
            if assessment.missing_preconditions:
                code = f"{case.case_id}_PRECONDITION_MISSING"
                message = "Factor 4.0 计算子项缺少原始数据或受控 fixture 前置。"
                evidence: dict[str, object] = {
                    "missing_preconditions": list(assessment.missing_preconditions),
                    "missing_reason_codes": list(assessment.missing_reason_codes),
                    "required_preconditions": list(case.required_preconditions),
                    "oracle": case.oracle,
                }
            else:
                code = "LIVE_ORACLE_EXECUTION_REQUIRED"
                message = "前置已齐备，但尚未执行独立真实计算 oracle。"
                evidence = {
                    "required_preconditions": list(case.required_preconditions),
                    "oracle": case.oracle,
                    "reason": "fixture readiness is not a product result",
                }
            checks.append(
                CalculationCheckResult(
                    case_id=case.case_id,
                    title=case.title,
                    status="BLOCKED_DATA_PRECONDITION",
                    summary=(
                        "缺少前置: "
                        + ", ".join(assessment.missing_reason_codes)
                        if assessment.missing_preconditions
                        else "等待独立真实计算 oracle"
                    ),
                    checked_count=0,
                    findings=(
                        CalculationIssue(
                            status="BLOCKED_DATA_PRECONDITION",
                            code=code,
                            message=message,
                            evidence=evidence,
                        ),
                    ),
                    evidence={
                        "module": case.module,
                        "mode": case.mode,
                        "oracle": case.oracle,
                    },
                )
            )
        return tuple(checks)

    @staticmethod
    def _validate_cases(cases: Sequence[Factor4CoverageCase]) -> None:
        """Validate manifest identity and precondition shape.

        ``ValueError`` identifies malformed manifests before they can enter a
        report.  This helper performs no external access.
        """

        seen_ids: set[str] = set()
        for case in cases:
            if not isinstance(case, Factor4CoverageCase):
                raise ValueError("Factor 4.0 coverage manifest contains an invalid case object")
            if not isinstance(case.case_id, str) or not case.case_id.strip():
                raise ValueError("Factor 4.0 coverage case_id must not be blank")
            if case.case_id in seen_ids:
                raise ValueError(f"duplicate Factor 4.0 coverage case_id: {case.case_id}")
            seen_ids.add(case.case_id)
            if (
                not isinstance(case.module, str)
                or not case.module.strip()
                or not isinstance(case.title, str)
                or not case.title.strip()
                or not isinstance(case.oracle, str)
                or not case.oracle.strip()
            ):
                raise ValueError(f"{case.case_id} must declare module, title and oracle")
            if case.mode not in {"READ_ONLY", "R1_WRITE"}:
                raise ValueError(f"{case.case_id} has unsupported execution mode: {case.mode!r}")
            if not isinstance(case.required_preconditions, Sequence) or isinstance(
                case.required_preconditions, (str, bytes)
            ) or not case.required_preconditions:
                raise ValueError(f"{case.case_id} must declare preconditions")
            if any(not isinstance(name, str) for name in case.required_preconditions):
                raise ValueError(f"{case.case_id} preconditions must be strings")
            if len(set(case.required_preconditions)) != len(case.required_preconditions):
                raise ValueError(f"{case.case_id} declares duplicate preconditions")
            if any(not name.strip() for name in case.required_preconditions):
                raise ValueError(f"{case.case_id} contains a blank precondition")


def _missing_reason_code(name: str) -> str:
    """Normalize an internal capability name to the report's ``*_MISSING`` code."""

    normalized = name.strip().upper()
    return normalized if normalized.endswith("_MISSING") else f"{normalized}_MISSING"


__all__ = [
    "CoverageMode",
    "CoverageReadiness",
    "Factor4CalculationCoverageService",
    "Factor4CoverageAssessment",
    "Factor4CoverageCase",
    "PENDING_FACTOR4_CALCULATION_CASES",
]
