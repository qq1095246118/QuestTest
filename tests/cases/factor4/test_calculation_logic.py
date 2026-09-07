"""Factor 4.0 计算逻辑 R0 的测试环境结构化验收。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from service.factor4_calculation_service import (
        CalculationCheckResult,
        CalculationIssue,
        Factor4CalculationReport,
        Factor4CalculationService,
    )


_CASE_IDS: Final[tuple[str, ...]] = (
    "CALC-510-A",
    "CALC-501-C",
    "CALC-506-A",
    "CALC-507-A",
)
_PASS = "PASS"
_FAIL = "FAIL"
_FACTOR_REF_SAMPLE_LIMIT = 5
_SAFE_EVIDENCE_KEYS: Final[tuple[str, ...]] = (
    "reason",
    "path",
    "read",
    "scope",
    "field",
    "route_profile_key",
)
_SAFE_EVIDENCE_VALUE_LIMIT = 120
_SAFE_EVIDENCE_VALUES_LIMIT = 3
_SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)"
    r"(?:bearer\s+\S+|naf_mcp_\S+|"
    r"(?:mcp[-_ ]?session|session[-_ ]?(?:id|token|secret))\s*[:=]?\s*\S+|"
    r"(?:password|authorization|access[_-]?token|refresh[_-]?token|secret)\s*[:=]\s*\S+|"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
)
_BLOCKED_STATUSES: Final[frozenset[str]] = frozenset(
    {"BLOCKED_DATA_PRECONDITION", "BLOCKED_DOC"}
)
_ALL_STATUSES: Final[frozenset[str]] = frozenset({_PASS, _FAIL, *_BLOCKED_STATUSES})

pytestmark = [
    pytest.mark.integration,
    pytest.mark.regression,
    pytest.mark.factor4_calculation,
]


@pytest.fixture(scope="module")
def factor4_calculation_report(
    factor4_calculation_service: Factor4CalculationService,
) -> Factor4CalculationReport:
    """只执行最终结果及 MCP 输出校验并共享不可变结构化报告。

    参数 ``factor4_calculation_service`` 是 conftest 按 live/test 门禁组装的业务服务。返回
    ``Factor4CalculationReport``，供四个 Case 独立判定；不执行静态算子数学扫描，网络、协议或数据库异常不吞掉。
    """

    return factor4_calculation_service.run_result_checks(
        market_scope="all",
        route_profile_key="default",
    )


@pytest.mark.factor4_internal_calculation
def test_factor4_r0_internal_formula_regressions(
    factor4_calculation_service: Factor4CalculationService,
) -> None:
    """显式专项保留历史 CALC-510-C；不进入默认结果级套件。

    使用原 R0 方法执行数学回归并读取同一结构化结果；传输错误向上传播，
    缺少历史数学证据仍按原规则报告 skip，不改写成产品缺陷。
    """
    report = factor4_calculation_service.run_r0_checks("all", "default")
    _assert_calculation_check(report, "CALC-510-C")


@pytest.mark.parametrize("case_id", _CASE_IDS, ids=_CASE_IDS)
def test_factor4_r0_calculation_check(
    factor4_calculation_report: Factor4CalculationReport,
    case_id: str,
) -> None:
    """按单项结构化状态把计算结果映射为 passed、failed 或明确 skipped。

    参数 ``factor4_calculation_report`` 是 Service 单次执行结果，``case_id`` 是当前子项标识。不返回值；仅
    ``PASS`` 且存在实际检查证据时通过，``FAIL`` 调用 ``pytest.fail``，数据或文档阻断调用 ``pytest.skip``，
    未知状态、重复/缺失子项和空证据均失败。skip 消息保留阻断类型，供 ``-ra`` 和 JUnit 报告读取。
    """

    _assert_calculation_check(factor4_calculation_report, case_id)


def _assert_calculation_check(
    factor4_calculation_report: Factor4CalculationReport, case_id: str,
) -> None:
    """Share structured-result assertions without executing another business Case."""

    check = _require_check(factor4_calculation_report, case_id)
    diagnostic = _diagnostic(factor4_calculation_report, check)
    if check.status in _BLOCKED_STATUSES:
        pytest.skip(diagnostic)
    if check.status == _FAIL:
        pytest.fail(diagnostic, pytrace=False)
    if check.status != _PASS:
        pytest.fail(f"{case_id}: unknown structured status {check.status!r}", pytrace=False)
    if check.checked_count <= 0:
        pytest.fail(
            f"{case_id}: Service returned PASS without checked evidence; {diagnostic}",
            pytrace=False,
        )


def _require_check(
    report: Factor4CalculationReport,
    case_id: str,
) -> CalculationCheckResult:
    matches = [check for check in report.checks if check.case_id == case_id]
    if len(matches) != 1:
        pytest.fail(
            f"{case_id}: expected exactly one structured check, found {len(matches)}",
            pytrace=False,
        )
    check = matches[0]
    if check.status not in _ALL_STATUSES:
        pytest.fail(
            f"{case_id}: unsupported structured status {check.status!r}",
            pytrace=False,
        )
    if report.market_scope != "all" or report.route_profile_key != "default":
        pytest.fail(
            f"{case_id}: report partition changed to "
            f"{report.market_scope!r}/{report.route_profile_key!r}",
            pytrace=False,
        )
    return check


def _diagnostic(
    report: Factor4CalculationReport,
    check: CalculationCheckResult,
) -> str:
    grouped: dict[str, list[CalculationIssue]] = {}
    for finding in check.findings:
        grouped.setdefault(finding.code, []).append(finding)

    finding_parts: list[str] = []
    status_priority = {
        _PASS: 0,
        "BLOCKED_DOC": 1,
        "BLOCKED_DATA_PRECONDITION": 2,
        _FAIL: 3,
    }
    for code, findings_for_code in grouped.items():
        representative = max(
            findings_for_code,
            key=lambda finding: status_priority[finding.status],
        )
        count = 0
        factor_ref_count = 0
        factor_refs: set[str] = set()
        for finding in findings_for_code:
            stored_count = finding.evidence.get("count")
            count += (
                stored_count
                if isinstance(stored_count, int) and not isinstance(stored_count, bool) and stored_count > 0
                else 1
            )
            if finding.factor_ref:
                factor_refs.add(finding.factor_ref)
            stored_refs = finding.evidence.get("factor_ref_samples")
            if isinstance(stored_refs, list):
                factor_refs.update(ref for ref in stored_refs if isinstance(ref, str) and ref)
            stored_ref_count = finding.evidence.get("factor_ref_count")
            if isinstance(stored_ref_count, int) and not isinstance(stored_ref_count, bool):
                factor_ref_count += max(stored_ref_count, 0)
        factor_ref_count = max(factor_ref_count, len(factor_refs))
        samples = sorted(factor_refs)[:_FACTOR_REF_SAMPLE_LIMIT]
        safe_evidence = _safe_evidence_summary(findings_for_code)
        evidence_suffix = f"; evidence={safe_evidence}" if safe_evidence else ""
        finding_parts.append(
            f"{representative.status}:{code} count={count}, "
            f"factor_ref_count={factor_ref_count}, factor_ref_samples={samples}: "
            f"{representative.message}{evidence_suffix}"
        )
    findings = "; ".join(finding_parts) if finding_parts else "none"
    return (
        f"{check.status}: {check.case_id} {check.title}; {check.summary}; "
        f"checked_count={check.checked_count}; batch_uid={report.batch_uid or 'unavailable'}; "
        f"findings={findings}"
    )


def _safe_evidence_summary(findings: Sequence[CalculationIssue]) -> str:
    """Render a bounded, whitelisted subset of finding evidence.

    参数 ``findings`` 是同一错误代码的结构化 findings。返回仅包含少量允许的
    标量证据（如阻断原因和配置路径）；未知字段、嵌套响应正文及疑似凭据值会被
    忽略或替换，且每个值有长度和数量上限。
    """

    parts: list[str] = []
    for key in _SAFE_EVIDENCE_KEYS:
        values: list[str] = []
        for finding in findings:
            value = _safe_evidence_value(finding.evidence.get(key))
            if value is not None and value not in values:
                values.append(value)
            if len(values) >= _SAFE_EVIDENCE_VALUES_LIMIT:
                break
        if values:
            parts.append(f"{key}={values}")
    return ", ".join(parts)


def _safe_evidence_value(value: object) -> str | None:
    """Normalize one diagnostic evidence scalar without exposing secrets.

    参数 ``value`` 是 finding evidence 中待展示的值。返回长度受限的安全字符串；
    非标量、空值或命中 Token/Session/JWT 模式时返回 ``None`` 或 ``[REDACTED]``。
    不抛出异常。
    """

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    if _SENSITIVE_DIAGNOSTIC_PATTERN.search(normalized):
        return "[REDACTED]"
    return normalized[:_SAFE_EVIDENCE_VALUE_LIMIT]
