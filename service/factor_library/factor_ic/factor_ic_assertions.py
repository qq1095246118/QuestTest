from __future__ import annotations

from typing import Any

from service.common.http.json_response_assertion import JSONResponseAssertionService


class FactorICAssertionService:
    """FactorIC 接口响应断言服务。

    请求参数:
        不需要实例化，直接通过静态方法校验 IC 汇总、切片、运行记录响应。
    返回值:
        错误信息列表；空列表表示响应符合接口自身规则。
    """

    @staticmethod
    def success_errors(status_code: int, body: Any) -> list[str]:
        """校验 FactorIC 成功响应信封。

        请求参数:
            status_code: HTTP 状态码。
            body: 接口 JSON 响应体。
        返回值:
            错误信息列表。
        """
        errors = []
        if status_code != 200:
            errors.append(f"status_code must be 200, got {status_code}")
        errors.extend(JSONResponseAssertionService.success_errors(body))
        return errors

    @staticmethod
    def accepted_or_success_errors(status_code: int, body: Any) -> list[str]:
        """校验创建类 IC 接口响应。

        请求参数:
            status_code: HTTP 状态码。
            body: 接口 JSON 响应体。
        返回值:
            错误信息列表。
        """
        if status_code not in {200, 202}:
            return [f"status_code must be 200 or 202, got {status_code}"]
        return JSONResponseAssertionService.success_errors(body)

    @staticmethod
    def metric_list_contains_errors(
        body: Any,
        expected_factor_id: int,
        expected_run_id: str,
        expected_symbol: str | None = None,
        required_metric_keys: tuple[str, ...] = (),
    ) -> list[str]:
        """校验 IC 指标列表包含本次写入的指标记录。

        请求参数:
            body: IC summary-metrics 或 slice-metrics 列表接口 JSON 响应体。
            expected_factor_id: 本次写入指标的因子或子因子 ID。
            expected_run_id: 本次写入指标的 run_id。
            expected_symbol: 可选交易对；slice metrics 场景用于进一步限定记录。
            required_metric_keys: 目标记录上必须存在且不为空的关键指标字段。
        返回值:
            错误信息列表；空列表表示列表中存在本次写入记录且关键指标字段完整。
        """
        errors = JSONResponseAssertionService.success_errors(body)
        if errors:
            return errors

        data = body["data"]
        items = data.get("items") if isinstance(data, dict) else data if isinstance(data, list) else None
        if not isinstance(items, list):
            return ["data.items must be list"]

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("factor_id") != expected_factor_id:
                continue
            if item.get("run_id") != expected_run_id:
                continue
            if expected_symbol is not None and item.get("symbol") != expected_symbol:
                continue

            missing_metric_keys = [key for key in required_metric_keys if item.get(key) is None]
            if missing_metric_keys:
                return [f"metric item missing keys: {', '.join(missing_metric_keys)}"]
            return []

        target = f"factor_id={expected_factor_id}, run_id={expected_run_id}"
        if expected_symbol is not None:
            target = f"{target}, symbol={expected_symbol}"
        return [f"metric item not found for {target}"]
