"""账号管理总览的真实接口数据用例。

所有数据来自一次性策略侧数据集；本文件只调用 GET，不创建账号、不写快照、不清理数据。
"""

from __future__ import annotations

from typing import Any

import pytest

from api.account_management_api import ACCOUNT_MANAGEMENT_METRICS


pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.account_management]

CORE_METRICS = tuple(ACCOUNT_MANAGEMENT_METRICS.split(","))
VALID_STATUSES = {"complete", "partial", "estimated", "unavailable"}


def _success(response: dict[str, Any], endpoint: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """断言真实响应 envelope，返回 data 和 meta。"""

    assert response.get("http_status") == 200, {"endpoint": endpoint, "response": response}
    body = response.get("body")
    assert isinstance(body, dict), response
    assert body.get("success") is True, body
    meta = body.get("meta")
    data = body.get("data")
    assert isinstance(meta, dict), body
    assert isinstance(data, dict), body
    assert meta.get("mock") is False, body
    return data, meta


def _assert_metric_objects(metrics: Any) -> None:
    """逐项验证 16 个核心指标的结构化状态和值。"""

    assert isinstance(metrics, dict), metrics
    missing = sorted(set(CORE_METRICS) - set(metrics))
    assert not missing, {"missing_metrics": missing, "metrics": metrics}
    for name in CORE_METRICS:
        metric = metrics[name]
        assert isinstance(metric, dict), {"metric": name, "value": metric}
        assert metric.get("status") in VALID_STATUSES, {"metric": name, "value": metric}
        if metric.get("value") is None:
            assert metric.get("reason_code") or metric.get("status") == "unavailable", {
                "metric": name,
                "value": metric,
            }


def test_account_management_dataset_directory_is_multi_account_and_traceable(
    account_management_data_default_page,
    persistent_data_preflight,
) -> None:
    """AM-DATA-001：目录必须返回持久化数据集中的多个账号和核心字段。"""

    snapshot = persistent_data_preflight.require(
        "account_management", "multi_account", "published_generation", case_id="AM-DATA-001"
    )
    data, meta = _success(account_management_data_default_page, "/admin/quant-accounts")
    assert data.get("context", {}).get("subject_type") in {"portfolio", "account_directory", None}, data
    items = data.get("items")
    assert isinstance(items, list), data
    assert len(items) >= 2, data
    returned_ids = {
        str(item.get(key))
        for item in items
        if isinstance(item, dict)
        for key in ("account_id", "accountId", "id", "AccountID")
        if item.get(key) is not None
    }
    assert returned_ids, data
    assert returned_ids.intersection({str(value) for value in snapshot.account_ids}), {
        "returned_ids": returned_ids,
        "dataset_accounts": snapshot.account_ids,
    }
    for item in items:
        if isinstance(item, dict) and "metrics" in item:
            _assert_metric_objects(item["metrics"])
    assert meta.get("snapshot_id"), meta


def test_account_management_portfolio_summary_contains_all_core_metrics(
    account_management_data_default_summary,
    persistent_data_preflight,
) -> None:
    """AM-DATA-002：portfolio 摘要返回 16 个指标及可追踪发布身份。"""

    snapshot = persistent_data_preflight.require(
        "account_management", "multi_account", "performance_samples", "published_generation",
        case_id="AM-DATA-002",
    )
    data, meta = _success(
        account_management_data_default_summary,
        "/quantitative-trading/analytics/summary",
    )
    assert data.get("context", {}).get("subject_type") == "portfolio", data
    _assert_metric_objects(data.get("metrics"))
    assert meta.get("snapshot_id"), meta
    assert len(snapshot.account_ids) >= 2


def test_account_management_total_return_is_available_for_seeded_runtime(
    account_management_data_default_summary,
    persistent_data_preflight,
) -> None:
    """AM-DATA-003：已发布的多账号运行集必须能给出总收益率状态和值。"""

    persistent_data_preflight.require(
        "account_management", "multi_account", "performance_samples", "published_generation",
        case_id="AM-DATA-003",
    )
    data, _ = _success(account_management_data_default_summary, "/quantitative-trading/analytics/summary")
    metric = data["metrics"]["cumulative_return"]
    assert metric.get("status") in {"complete", "estimated", "partial"}, metric
    assert metric.get("value") is not None, metric


def test_account_management_explicit_dataset_accounts_keep_portfolio_scope(
    account_management_api_live,
    persistent_data_preflight,
) -> None:
    """AM-DATA-004：按数据集账号集合过滤仍按 portfolio 维度返回。"""

    snapshot = persistent_data_preflight.require(
        "account_management", "multi_account", "published_generation", case_id="AM-DATA-004"
    )
    account_ids = [str(value) for value in snapshot.account_ids]
    response = account_management_api_live.summary(account_ids=account_ids)
    data, meta = _success(response, "/quantitative-trading/analytics/summary")
    params = response.get("request_params")
    assert isinstance(params, dict), response
    assert params.get("account_ids") == ",".join(account_ids), params
    assert data.get("context", {}).get("subject_type") == "portfolio", data
    assert meta.get("snapshot_id"), meta


def test_account_management_default_request_has_no_snapshot_input(account_management_api_live) -> None:
    """AM-PROTOCOL-001：页面默认请求不携带 snapshot_id 且返回真实 envelope。"""

    response = account_management_api_live.accounts_page()
    data, meta = _success(response, "/admin/quant-accounts")
    params = response.get("request_params")
    assert isinstance(params, dict), response
    assert "snapshot_id" not in params, params
    assert params.get("metrics") == ACCOUNT_MANAGEMENT_METRICS, params
    assert isinstance(data.get("items"), list), data
    assert meta.get("mock") is False, meta


def test_account_management_unknown_account_filter_does_not_fallback(account_management_api_live) -> None:
    """AM-PROTOCOL-002：未知账号筛选返回空集合，不回退全量账号。"""

    response = account_management_api_live.accounts_page(account_ids=["999999999999"], limit=100)
    data, _ = _success(response, "/admin/quant-accounts")
    assert data.get("items") == [], data
