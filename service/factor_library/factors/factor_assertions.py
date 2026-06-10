from __future__ import annotations

from typing import Any

from service.common.http.json_response_assertion import JSONResponseAssertionService


class FactorAssertionService:
    """因子接口响应断言服务。

    请求参数:
        不需要实例化，直接通过静态方法校验因子、主题、子因子和元数据接口响应。
    返回值:
        错误信息列表；空列表表示响应符合接口自身规则。
    """

    @staticmethod
    def success_with_data_errors(status_code: int, body: Any) -> list[str]:
        """校验通用成功响应。

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
    def list_pagination_errors(body: Any, expected_page: int, expected_limit: int) -> list[str]:
        """校验列表分页结构。

        请求参数:
            body: 列表接口 JSON 响应体。
            expected_page: 期望页码。
            expected_limit: 期望每页条数。
        返回值:
            错误信息列表。
        """
        errors = JSONResponseAssertionService.success_errors(body)
        if errors:
            return errors
        data = body["data"]
        if not isinstance(data, dict):
            return ["data must be dict"]
        if not isinstance(data.get("items"), list):
            errors.append("data.items must be list")
        pagination = data.get("pagination")
        if not isinstance(pagination, dict):
            errors.append("data.pagination must be dict")
            return errors
        if pagination.get("page") != expected_page:
            errors.append(f"pagination.page mismatch: expected={expected_page}, actual={pagination.get('page')}")
        if pagination.get("limit") != expected_limit:
            errors.append(f"pagination.limit mismatch: expected={expected_limit}, actual={pagination.get('limit')}")
        if len(data.get("items", [])) > expected_limit:
            errors.append("items length must not exceed expected limit")
        return errors
