"""Reconcile route candidates from final metrics without inferring publication policy."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from db.factor4_calculation_repository import CalculationAuditSnapshot
from service.factor4_calculation_service import (
    CalculationCheckResult,
    CalculationIssue,
    _blocked,
    _blocked_doc,
    _configured_route_profile_weights,
    _failed,
    _metric_pair_identity,
    _metric_pair_key,
    _metric_pairs,
    _result,
    _same_route_metric_identity,
    _to_decimal,
)
from service.factor4_result_service import ENVIRONMENT_LABELS


class Factor4RouteMembershipService:
    """Start from all final metrics, including candidates omitted from route tables."""

    @staticmethod
    def check_candidates(
        snapshot: CalculationAuditSnapshot, label_code: str,
    ) -> CalculationCheckResult:
        """Compare final-score candidates with active eligible routes for one label.

        Input is a consistent DB snapshot, not route evidence or summary counts.
        Output contains candidates, excluded pairs and unpublished differences.
        Known inadmissible published rows fail. Missing candidates block pending
        Backend selection rules, since eligibility alone does not imply publication.
        No I/O occurs; an unknown environment raises ValueError.
        """

        if label_code not in ENVIRONMENT_LABELS:
            raise ValueError("unknown environment label")
        metrics = tuple(m for m in snapshot.evaluation_metrics if m.label_code == label_code)
        pairs, pair_issues = _metric_pairs(metrics)
        issues: list[CalculationIssue] = list(pair_issues)
        multiplicity = Counter((_metric_pair_key(m), m.evaluation_type) for m in metrics)
        weights, weight_path, weight_error = _configured_route_profile_weights(snapshot.batch)
        minimum = _to_decimal(snapshot.batch.evaluation_config.get("minimum_route_score"))
        candidates: list[dict[str, object]] = []
        excluded_ids: set[int] = set()
        classified_ids: set[int] = set()
        checked = 0
        if not metrics:
            issues.append(_blocked("ROUTE_CANDIDATE_METRICS_MISSING", "目标环境缺少最终指标，不能推断应有集合。"))
        for key, pair in pairs.items():
            ids = sorted(m.id for m in pair.values())
            if any(multiplicity[(key, scope)] != 1 for scope in pair):
                continue
            if any(_metric_pair_identity(m) is None for m in pair.values()):
                continue
            if set(pair) != {"time_series", "cross_sectional"}:
                issues.append(_blocked("ROUTE_CANDIDATE_PAIR_INCOMPLETE", "候选缺少完整 TS/CS 结果，不能把缺行当无效维度。", metric_ids=ids))
                continue
            if any(
                m.eval_batch_id != snapshot.batch.id or m.market_scope != snapshot.batch.market_scope
                or m.label_kind != snapshot.batch.label_kind for m in pair.values()
            ):
                issues.append(_failed("ROUTE_CANDIDATE_PARTITION_MISMATCH", "候选指标混入其他批次或市场分区。", metric_ids=ids))
                continue
            if any(m.scoring_version != "env-score-v1" for m in pair.values()) or snapshot.batch.score_rule_version != "env-score-v1":
                issues.append(_blocked_doc("ROUTE_CANDIDATE_SCORE_VERSION_UNSUPPORTED", "候选集合缺少对应评分版本规则。", metric_ids=ids))
                continue
            if any(m.metric_status not in {"success", "insufficient_sample", "failed"} for m in pair.values()):
                issues.append(_blocked("ROUTE_CANDIDATE_STATUS_UNRESOLVED", "候选仍有非终态或未知指标状态。", metric_ids=ids))
                continue
            if any(m.metric_status == "success" and not isinstance(m.is_valid, bool) for m in pair.values()):
                issues.append(_blocked("ROUTE_CANDIDATE_VALIDITY_MISSING", "成功指标缺少明确有效性。", metric_ids=ids))
                continue
            valid = {s: m for s, m in pair.items() if m.metric_status == "success" and m.is_valid is True}
            if not valid:
                excluded_ids.update(ids)
                classified_ids.update(ids)
                checked += 1
                continue
            if weights is None or minimum is None:
                issues.append(_blocked("ROUTE_CANDIDATE_FROZEN_CONFIG_MISSING", "缺少冻结权重或最低分，不能独立判定候选集合。", metric_ids=ids, weight_path=weight_path, weight_error=weight_error))
                continue
            values = {
                s: (_to_decimal(m.time_series_score if s == "time_series" else m.cross_sectional_score), _to_decimal(m.confidence))
                for s, m in valid.items()
            }
            if any(score is None or conf is None for score, conf in values.values()):
                issues.append(_blocked("ROUTE_CANDIDATE_COMPONENT_MISSING", "有效维度缺少最终分数或置信度。", metric_ids=ids))
                continue
            if any(not 0 <= score <= 100 or not 0 <= conf <= 1 for score, conf in values.values()):
                issues.append(_failed("ROUTE_CANDIDATE_COMPONENT_INVALID", "候选维度分数或置信度越界。", metric_ids=ids))
                continue
            total_weight = sum(weights[s] for s in valid)
            if total_weight <= 0:
                issues.append(_blocked_doc("ROUTE_CANDIDATE_ZERO_WEIGHT", "有效维度总权重为零，缺少合成处理规则。", metric_ids=ids))
                continue
            base = sum(score * weights[s] / total_weight for s, (score, _) in values.items())
            confidence = sum(conf * weights[s] / total_weight for s, (_, conf) in values.items())
            raw_score = base * confidence
            floor_score = raw_score.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            score = raw_score.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if raw_score - floor_score == Decimal("0.0000005") and (floor_score >= minimum) != (score >= minimum):
                issues.append(_blocked_doc("ROUTE_CANDIDATE_ROUNDING_MIDPOINT_UNSPECIFIED", "精确舍入中点会改变候选入选，缺少中点舍入策略。", metric_ids=ids))
                continue
            classified_ids.update(ids)
            checked += 1
            if score < minimum:
                excluded_ids.update(ids)
            else:
                first = next(iter(pair.values()))
                candidates.append({"factor_ref": first.factor_ref, "factor_version": first.factor_version,
                                   "metric_ids": ids, "routing_score": str(score)})

        actual = [r for r in snapshot.routes if r.label_code == label_code and r.is_active and r.is_eligible]
        actual_ids: set[int] = set()
        metrics_by_id: dict[int, list[object]] = {}
        for metric in metrics:
            metrics_by_id.setdefault(metric.id, []).append(metric)
        for route in actual:
            referenced = metrics_by_id.get(route.metric_id, [])
            fields = ("eval_batch_id", "publication_uid", "publish_version", "market_scope",
                      "route_profile_key", "label_kind", "as_of_time", "score_rule_version")
            correct_publication = all(
                getattr(route, field) == getattr(snapshot.batch, "id" if field == "eval_batch_id" else field)
                for field in fields
            )
            if len(referenced) != 1 or not _same_route_metric_identity(route, referenced[0]) or not correct_publication:
                issues.append(_failed("ROUTE_CANDIDATE_REFERENCE_MISMATCH", "已发布路由身份或版本与唯一候选指标及发布批次不一致。", route.factor_ref, route_id=route.id))
                continue
            actual_ids.add(route.metric_id)
            if route.metric_id in excluded_ids:
                issues.append(_failed("ROUTE_PUBLISHED_OUTSIDE_FINAL_CANDIDATES", "已发布有效路由不满足最终指标准入或最低分。", route.factor_ref, route_id=route.id, metric_id=route.metric_id))
            elif route.metric_id not in classified_ids:
                issues.append(_blocked("ROUTE_MEMBERSHIP_UNCLASSIFIED", "已发布路由尚无法关联可判定的完整候选。", route.factor_ref, route_id=route.id))
        unpublished = [c for c in candidates if not actual_ids.intersection(c["metric_ids"])]
        if unpublished:
            issues.append(_blocked_doc(
                "ROUTE_PUBLICATION_SELECTION_RULE_REQUIRED",
                "具备评分资格的候选未出现在路由中；缺少 Backend 全量入选、TopN、状态及发布确认规则，暂不定性漏发布。",
                candidate_count=len(unpublished),
            ))
        return _result(
            "RESULT-508", "最终指标候选集合与已发布路由差异", checked, issues,
            label_code=label_code, candidate_count=len(candidates), candidates=candidates,
            batch_id=snapshot.batch.id, market_scope=snapshot.batch.market_scope,
            route_profile_key=snapshot.batch.route_profile_key,
            publication_uid=snapshot.batch.publication_uid,
            unpublished_candidates=unpublished, actual_eligible_route_count=len(actual),
            excluded_pair_count=len(excluded_ids) // 2,
            full_publication_policy_verified=False,
        )
