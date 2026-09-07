"""最终费用结果及 route 证据的独立一致性检查，不冒充原始收益/换手重算。"""

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from service.factor4_read_service import ReadCheck, ReadPrecondition

_FIELDS = ("turnover_rate", "net_return", "sharpe")


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and abs(a-b) <= Decimal("0.0000000000005")
    except InvalidOperation:
        return False


def check_published_cost_results(metrics: tuple[dict[str, Any], ...], routes: tuple[dict[str, Any], ...]) -> ReadCheck:
    """核对 success/非success费用空值、payload数值和route scope引用身份；无成功指标明确前置，不执行I/O。"""
    if not any(row.get("metric_status") == "success" for row in metrics):
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no successful published cost results")
    issues: list[str] = []
    by_id = {row["id"]: row for row in metrics}
    for row in metrics:
        prefix = f"cost:metric={row['id']}:"
        if row.get("metric_status") == "success":
            payload = _payload(row.get("metric_payload"))
            for key in _FIELDS:
                if row.get(key) is None:
                    issues.append(prefix + key + "_missing")
                if key not in payload or not _equal(row.get(key), payload.get(key)):
                    issues.append(prefix + key + "_payload")
        elif any(row.get(key) is not None for key in _FIELDS):
            issues.append(prefix + "nonsuccess_cost_not_null")
    for route in routes:
        prefix = f"cost:route={route['id']}:"
        evidence = _payload(route.get("evidence"))
        ids = evidence.get("metric_ids")
        if not isinstance(ids, dict) or not ids:
            issues.append(prefix + "metric_ids_missing")
            continue
        if route.get("metric_id") not in ids.values():
            issues.append(prefix + "primary_metric_not_traced")
        for scope, metric_id in ids.items():
            metric = by_id.get(metric_id)
            nested = evidence.get(scope)
            if metric is None or not isinstance(nested, dict):
                issues.append(prefix + "metric_or_scope_missing")
                continue
            if metric.get("evaluation_type") != scope or any(metric.get(key) != route.get(key) for key in ("factor_ref", "label_code", "market_scope", "eval_batch_id")):
                issues.append(prefix + "scope_identity")
            for key in _FIELDS:
                if key not in nested or not _equal(metric.get(key), nested.get(key)):
                    issues.append(prefix + key + "_evidence")
    return ReadCheck(len(metrics) + len(routes), tuple(dict.fromkeys(issues)))
