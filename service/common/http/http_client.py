"""HTTP 基础设施模块。

本模块只负责统一 HTTP 请求、超时和可重试状态码处理，不承载业务判断。
"""

import logging

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class HTTPClient:
    """统一 HTTP 请求客户端。

    请求参数:
        不需要实例化，直接通过静态方法发起 HTTP 请求。
    返回值:
        提供带超时和临时错误重试能力的请求方法。
    """

    @staticmethod
    def is_retryable_http_error(exc: BaseException) -> bool:
        """判断 HTTPError 是否属于可重试的临时网关错误。

        请求参数:
            exc: 请求过程中抛出的异常。
        返回值:
            True 表示异常是 429、502、503 或 504，可由 tenacity 重试。
        """
        response = getattr(exc, "response", None)
        return (
            isinstance(exc, requests.exceptions.HTTPError)
            and response is not None
            and response.status_code in RETRYABLE_STATUS_CODES
        )

    @staticmethod
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(lambda exc: HTTPClient.is_retryable_http_error(exc)),
        reraise=True,
    )
    def request(method: str, url: str, **kwargs):
        """发送 HTTP 请求并对临时网关错误进行指数退避重试。

        请求参数:
            method: HTTP 方法。
            url: 完整请求 URL。
            **kwargs: requests.request 支持的其他参数，例如 headers、params、json、timeout。
        返回值:
            requests.Response 对象；非成功状态码会抛出 HTTPError。
        """
        kwargs.setdefault("timeout", 30)
        response = requests.request(method, url, **kwargs)
        if response.status_code in RETRYABLE_STATUS_CODES:
            logger.warning(
                "Retryable HTTP %s on %s",
                response.status_code,
                response.url,
            )

        response.raise_for_status()
        return response
