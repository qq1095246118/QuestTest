"""HTTP 基础设施模块。

本模块只负责统一 HTTP 请求、超时和可重试状态码处理，不承载业务判断。
"""

import logging

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _is_retryable_http_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and response is not None
        and response.status_code in RETRYABLE_STATUS_CODES
    )


class HTTPClient:
    @staticmethod
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def request(method: str, url: str, **kwargs):
        """
        Unified request wrapper with exponential backoff for transient gateway errors.
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
