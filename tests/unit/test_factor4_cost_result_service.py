"""最终费用证据断言正反例；不冒充原始计算重算。"""

from copy import deepcopy
from typing import Any

import pytest

from service.factor4_cost_result_service import check_published_cost_results

pytestmark = pytest.mark.unit


def sample() -> tuple[dict[str, Any], dict[str, Any]]:
    """返回完整指标和同身份route的费用证据，无网络/DB。"""
    values = {"turnover_rate": .3, "net_return": .2, "sharpe": 1.2}
    metric = {"id": 1, "eval_batch_id": 2, "factor_ref": "sub_factor:3", "label_code": "WIDE_RANGE", "market_scope": "all", "evaluation_type": "time_series", "metric_status": "success", **values, "metric_payload": values}
    route = {"id": 4, "eval_batch_id": 2, "factor_ref": "sub_factor:3", "label_code": "WIDE_RANGE", "market_scope": "all", "metric_id": 1, "evidence": {"metric_ids": {"time_series": 1}, "time_series": values}}
    return deepcopy(metric), deepcopy(route)


def test_cost_results_exact_trace_passes() -> None:
    metric, route = sample()
    assert not check_published_cost_results((metric,), (route,)).issues


@pytest.mark.parametrize("mutation", ["null_success", "payload", "wrong_scope", "wrong_batch", "missing_evidence", "wrong_evidence"])
def test_cost_evidence_corruption_is_not_ignored(mutation: str) -> None:
    metric, route = sample()
    if mutation == "null_success":
        metric["net_return"] = None
    elif mutation == "payload":
        metric["metric_payload"]["net_return"] = .7
    elif mutation == "wrong_scope":
        route["label_code"] = "NARROW_RANGE"
    elif mutation == "wrong_batch":
        route["eval_batch_id"] = 99
    elif mutation == "missing_evidence":
        route["evidence"]["time_series"] = {}
    else:
        route["evidence"]["time_series"]["net_return"] = .9
    assert check_published_cost_results((metric,), (route,)).issues
