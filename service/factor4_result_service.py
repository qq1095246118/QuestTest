"""Factor 4.0 已发布结果验收；不依赖原始行情、持仓或收益序列。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal

from db.factor4_calculation_repository import CalculationAuditSnapshot
from service.factor4_calculation_service import CalculationCheckResult, CalculationIssue


ENVIRONMENT_LABELS = (
    "UNILATERAL_UP", "CHOPPY_UP", "NARROW_RANGE", "WIDE_RANGE",
    "UNILATERAL_DOWN", "CHOPPY_DOWN",
)


class Factor4FinalResultService:
    """只验证已保存结果的内部一致性，不把保存值当作独立计算真值。"""

    @staticmethod
    def check_route_identity(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """核对 route 对应的 batch、版本、环境和唯一有效 metric。

        输入一致性只读快照，输出结构化结果；空 route 为数据前置阻断，明确的
        外键、身份或有效性矛盾为失败。不执行 I/O，不要求公式或逐 bar 数据。
        """

        issues: list[CalculationIssue] = []
        batch = snapshot.batch
        metrics_by_id: dict[int, list[object]] = {}
        for metric in snapshot.evaluation_metrics:
            metrics_by_id.setdefault(metric.id, []).append(metric)
        for route in snapshot.routes:
            for field, expected in (
                ("eval_batch_id", batch.id), ("publication_uid", batch.publication_uid),
                ("publish_version", batch.publish_version), ("market_scope", batch.market_scope),
                ("route_profile_key", batch.route_profile_key), ("label_kind", batch.label_kind),
                ("as_of_time", batch.as_of_time), ("score_rule_version", batch.score_rule_version),
                ("is_active", True),
            ):
                if getattr(route, field) != expected:
                    issues.append(_failure(
                        "RESULT_ROUTE_BATCH_MISMATCH", "已发布 route 与所属批次身份不一致",
                        route_id=route.id, field=field,
                    ))
            if route.label_code not in ENVIRONMENT_LABELS:
                issues.append(_failure(
                    "RESULT_ROUTE_LABEL_INVALID", "已发布 route 的环境标签无效", route_id=route.id,
                ))
            matches = metrics_by_id.get(route.metric_id, [])
            if len(matches) != 1:
                issues.append(_failure(
                    "RESULT_ROUTE_METRIC_NOT_UNIQUE", "已发布 route 无法关联唯一的同批次指标",
                    route_id=route.id, metric_id=route.metric_id, matched_count=len(matches),
                ))
                continue
            metric = matches[0]
            for field in (
                "eval_batch_id", "market_scope", "label_kind", "label_code", "factor_ref",
                "factor_type", "factor_id", "factor_version",
            ):
                if getattr(route, field) != getattr(metric, field):
                    issues.append(_failure(
                        "RESULT_ROUTE_METRIC_IDENTITY_MISMATCH", "已发布 route 引用了其他分区或版本的指标",
                        route_id=route.id, metric_id=route.metric_id, field=field,
                    ))
            if metric.scoring_version != route.score_rule_version:
                issues.append(_failure(
                    "RESULT_ROUTE_SCORING_VERSION_MISMATCH", "已发布 route 与指标评分版本不一致",
                    route_id=route.id, metric_id=route.metric_id,
                ))
            if route.is_eligible:
                if metric.metric_status != "success" or metric.is_valid is not True:
                    issues.append(_failure(
                        "RESULT_ROUTE_INVALID_METRIC", "合格 route 引用了无效或未成功的指标",
                        route_id=route.id, metric_id=route.metric_id,
                    ))
                if route.reject_reason_code:
                    issues.append(_failure(
                        "RESULT_ROUTE_ELIGIBLE_WITH_REJECTION", "合格 route 同时返回拒绝原因",
                        route_id=route.id,
                    ))
        return _result("RESULT-501", "已发布结果身份与准入一致性", len(snapshot.routes), issues)

    @staticmethod
    def check_value_domains(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """检查最终指标/分数取值域，不声称能证明 IC 或收益的计算正确。

        输入已保存的 metric 和 route；仅检查明确存在的字段及 v1 合格结果必需的
        分数/confidence。非法范围返回失败；无任何结果返回数据前置阻断。不执行 I/O。
        """

        issues: list[CalculationIssue] = []
        for metric in snapshot.evaluation_metrics:
            for field, low, high in (
                ("mean_ic", -1, 1), ("mean_rank_ic", -1, 1),
                ("coverage_rate", 0, 1), ("confidence", 0, 1),
                ("time_series_score", 0, 100), ("cross_sectional_score", 0, 100),
                ("routing_score", 0, 100), ("effective_sample_size", 0, None),
                ("turnover_rate", 0, None), ("oos_sign_consistency", 0, 1),
            ):
                value = getattr(metric, field)
                if value is not None and not _in_range(value, low, high):
                    issues.append(_failure(
                        "RESULT_METRIC_VALUE_OUT_OF_RANGE", "最终指标或分数超出合法取值范围",
                        metric_id=metric.id, field=field, actual=str(value),
                    ))
            if metric.metric_status == "success" and metric.is_valid is True and metric.scoring_version == "env-score-v1":
                score = metric.time_series_score if metric.evaluation_type == "time_series" else metric.cross_sectional_score
                if score is None or metric.confidence is None:
                    issues.append(_failure(
                        "RESULT_VALID_METRIC_SCORE_MISSING", "有效指标缺少最终维度分数或置信度",
                        metric_id=metric.id,
                    ))
        for route in snapshot.routes:
            for field, high in (("routing_score", 100), ("confidence", 1), ("time_series_score", 100), ("cross_sectional_score", 100)):
                value = getattr(route, field)
                required = route.is_eligible and field in {"routing_score", "confidence"}
                if (value is None and required) or (value is not None and not _in_range(value, 0, high)):
                    issues.append(_failure(
                        "RESULT_ROUTE_VALUE_INVALID", "已发布 route 的最终分数或置信度不合法",
                        route_id=route.id, field=field, actual=str(value),
                    ))
        return _result(
            "RESULT-502", "最终指标与分数取值域",
            len(snapshot.evaluation_metrics) + len(snapshot.routes), issues,
        )

    @staticmethod
    def check_environment_summary(
        snapshot: CalculationAuditSnapshot,
        label_code: str,
    ) -> CalculationCheckResult:
        """逐环境核对已发布摘要 route_count 与 active eligible route 实际数量。

        输入结果快照及六类环境之一；输出计数对账及 metric/route 覆盖证据。没有
        route 且摘要为 0 是一致的结果，不擅自要求每类环境必须有合格因子。摘要
        缺失为数据前置阻断，计数矛盾为失败；非法 label 抛 ValueError。不执行 I/O。
        """

        if label_code not in ENVIRONMENT_LABELS:
            raise ValueError("label_code must be one of the six environment labels")
        batch = snapshot.batch
        actual_count = sum(
            1 for route in snapshot.routes
            if route.is_active and route.is_eligible and route.label_code == label_code
            and route.label_kind == batch.label_kind and route.eval_batch_id == batch.id
            and route.publication_uid == batch.publication_uid and route.publish_version == batch.publish_version
        )
        evidence = {
            "label_code": label_code,
            "metric_count": sum(metric.label_code == label_code for metric in snapshot.evaluation_metrics),
            "eligible_route_count": actual_count,
        }
        status = batch.environment_status.get(label_code)
        if not isinstance(status, Mapping) or "route_count" not in status:
            return _result("RESULT-503", "环境结果摘要与明细对账", 0, [], evidence=evidence)
        declared = status["route_count"]
        issues: list[CalculationIssue] = []
        if isinstance(declared, bool) or not isinstance(declared, int) or declared < 0:
            issues.append(_failure(
                "ROUTE_ENVIRONMENT_ROUTE_COUNT_INVALID", "环境摘要中的 route_count 不是非负整数",
                label_code=label_code,
            ))
        elif declared != actual_count:
            issues.append(_failure(
                "ROUTE_ENVIRONMENT_ROUTE_COUNT_MISMATCH", "发布摘要路由数量与实际有效路由数量不一致",
                label_code=label_code, declared_route_count=declared, actual_route_count=actual_count,
            ))
        evidence["declared_route_count"] = declared
        return _result("RESULT-503", "环境结果摘要与明细对账", 1, issues, evidence=evidence)

    @staticmethod
    def check_route_evidence_consistency(
        snapshot: CalculationAuditSnapshot,
    ) -> CalculationCheckResult:
        """核对 route 中保存的最终分数、置信度和准入范围与 route 列一致。

        该检查只比较同一结果记录的字段，不重算评分公式；缺少 evidence 的记录
        为数据阻断，明确字段冲突为失败。输入为只读最终结果快照，不执行 I/O。
        """

        issues: list[CalculationIssue] = []
        for route in snapshot.routes:
            evidence = route.evidence
            if not isinstance(evidence, Mapping):
                issues.append(_failure("RESULT_ROUTE_EVIDENCE_MISSING", "route 缺少最终结果 evidence", route_id=route.id))
                continue
            valid_scopes = _scope_set(evidence.get("valid_scopes"))
            invalid_scopes = _scope_set(evidence.get("invalid_scopes"))
            if valid_scopes is None or invalid_scopes is None:
                issues.append(_failure("RESULT_ROUTE_SCOPE_EVIDENCE_INVALID", "route 的 valid/invalid scope 结果不合法", route_id=route.id))
            elif valid_scopes | invalid_scopes != {"time_series", "cross_sectional"} or valid_scopes & invalid_scopes:
                issues.append(_failure("RESULT_ROUTE_SCOPE_EVIDENCE_CONTRADICTORY", "route 的有效和无效维度结果互相矛盾", route_id=route.id))
            if route.is_eligible and (not valid_scopes or evidence.get("admission_mode") != "any_valid_scope"):
                issues.append(_failure("RESULT_ROUTE_ADMISSION_EVIDENCE_MISMATCH", "合格 route 的最终准入证据不支持任一维有效", route_id=route.id))
            for scope, field in (("time_series", "time_series_score"), ("cross_sectional", "cross_sectional_score")):
                scope_evidence = evidence.get(scope)
                if not isinstance(scope_evidence, Mapping):
                    continue
                _compare_final_decimal(route, scope_evidence.get("metric_score"), field, issues, route.id)
            _compare_final_decimal(route, evidence.get("routing_score"), "routing_score", issues, route.id)
            _compare_final_decimal(route, evidence.get("confidence"), "confidence", issues, route.id)
        return _result("RESULT-505", "route 最终结果 evidence 一致性", len(snapshot.routes), issues)

    @staticmethod
    def check_partition_isolation(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """验证最终 route 没有跨 batch、market、profile 或环境分区串线。

        只使用 route 的最终身份和 rank；同一环境日期分区内 rank 必须唯一且从 1
        开始。跨分区结果不能互相竞争。没有 route 时返回数据阻断，不执行 I/O。
        """

        batch = snapshot.batch
        issues: list[CalculationIssue] = []
        groups: dict[tuple[str, object], list[object]] = {}
        for route in snapshot.routes:
            for field, expected in (("eval_batch_id", batch.id), ("publication_uid", batch.publication_uid),
                                    ("publish_version", batch.publish_version), ("market_scope", batch.market_scope),
                                    ("route_profile_key", batch.route_profile_key)):
                if getattr(route, field) != expected:
                    issues.append(_failure("RESULT_PARTITION_LEAK", "最终 route 跨发布分区串线", route_id=route.id, field=field))
            if route.is_active and route.is_eligible:
                groups.setdefault((route.label_code, route.environment_date), []).append(route)
        for key, routes in groups.items():
            ranks = sorted(route.rank_no for route in routes)
            if ranks != list(range(1, len(ranks) + 1)):
                issues.append(_failure("RESULT_PARTITION_RANK_SEQUENCE_INVALID", "最终结果分区内 rank 不连续", partition=str(key), ranks=ranks))
            identities = [(route.factor_ref, route.factor_version) for route in routes]
            if len(identities) != len(set(identities)):
                issues.append(_failure("RESULT_PARTITION_FACTOR_DUPLICATE", "最终结果分区内因子版本重复", partition=str(key)))
        return _result("RESULT-506", "最终结果分区隔离", len(snapshot.routes), issues)

    @staticmethod
    def check_environment_matrix(snapshot: CalculationAuditSnapshot) -> CalculationCheckResult:
        """检查最终 route 的环境标签均属于同批次已声明环境结果。"""

        status = snapshot.batch.environment_status
        declared = {
            str(label) for label, value in status.items()
            if isinstance(value, Mapping) and value.get("status") == "success"
        } if isinstance(status, Mapping) else set()
        route_labels = {route.label_code for route in snapshot.routes if route.is_active}
        issues = [
            _failure("RESULT_ENVIRONMENT_LABEL_UNDECLARED", "route 使用了批次未声明成功的环境标签", label_code=label)
            for label in sorted(route_labels - declared)
        ]
        return _result("RESULT-507", "最终环境矩阵完整性", len(route_labels), issues,
                       evidence={"declared_labels": sorted(declared), "route_labels": sorted(route_labels)})


def _in_range(value: Decimal, low: int, high: int | None) -> bool:
    return value.is_finite() and value >= low and (high is None or value <= high)


def _scope_set(value: object) -> set[str] | None:
    """将最终结果中的 scope 列表转换为受限集合。"""

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return set(value)


def _compare_final_decimal(
    route: object,
    actual: object,
    field: str,
    issues: list[CalculationIssue],
    route_id: int,
) -> None:
    """比较 evidence 数值与 route 同名最终列；缺失列不臆测。"""

    if actual is None:
        return
    expected = getattr(route, field)
    try:
        parsed = actual if isinstance(actual, Decimal) else Decimal(str(actual))
    except Exception:
        issues.append(_failure("RESULT_ROUTE_EVIDENCE_NUMBER_INVALID", "route evidence 数值不是合法 Decimal", route_id=route_id, field=field))
        return
    if expected is not None and parsed != expected:
        issues.append(_failure("RESULT_ROUTE_EVIDENCE_VALUE_MISMATCH", "route evidence 与最终结果列不一致", route_id=route_id, field=field, expected=str(expected), actual=str(parsed)))


def _failure(code: str, message: str, **evidence: object) -> CalculationIssue:
    return CalculationIssue("FAIL", code, message, evidence=dict(evidence))


def _result(
    case_id: str,
    title: str,
    checked_count: int,
    issues: Sequence[CalculationIssue],
    *,
    evidence: Mapping[str, object] | None = None,
) -> CalculationCheckResult:
    status = "FAIL" if issues else "PASS" if checked_count else "BLOCKED_DATA_PRECONDITION"
    findings = tuple(issues) or (() if checked_count else (
        CalculationIssue("BLOCKED_DATA_PRECONDITION", "RESULT_EVIDENCE_MISSING", "缺少当前结果级检查所需的最终字段或记录"),
    ))
    codes = dict(Counter(item.code for item in findings))
    return CalculationCheckResult(
        case_id, title, status, f"{title}; checked={checked_count}; finding_counts={codes}",
        checked_count, findings,
        {"validation_level": "FINAL_RESULT_CONSISTENCY", **dict(evidence or {})},
    )
