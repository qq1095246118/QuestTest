from __future__ import annotations

from typing import Any

from requests import Response
from requests.exceptions import HTTPError


class HTTPResponseService:
    """HTTP 响应处理服务。

    请求参数:
        不需要实例化，直接通过静态方法处理 requests 响应和异常。
    返回值:
        提供 HTTP 异常响应恢复能力的 service 类。
    """

    @staticmethod
    def from_http_error(exc: HTTPError) -> Response:
        """从 requests.HTTPError 中取出原始响应对象。

        请求参数:
            exc: HTTPClient 在 4xx 或 5xx 状态码下抛出的 HTTPError。
        返回值:
            HTTPError 携带的 requests.Response 对象。
        """
        if exc.response is None:
            raise ValueError("HTTPError missing response")
        return exc.response

    @staticmethod
    def success_body_errors(body: Any) -> list[str]:
        """检查通用成功响应信封是否符合约定。

        请求参数:
            body: 接口响应 JSON 解析结果。
        返回值:
            错误信息列表；空列表表示响应包含 success=True 和 data。
        """
        errors = []
        if not isinstance(body, dict):
            return ["body must be dict"]
        if body.get("success") is not True:
            errors.append("success must be True")
        if "data" not in body:
            errors.append("body must contain data")
        return errors
