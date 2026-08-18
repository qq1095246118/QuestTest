"""通用 HTTP 客户端，只负责协议层行为。"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, Protocol

import requests

from config.settings import ApiSettings


class RequestSession(Protocol):
    """描述 HTTP 客户端所需的最小 Session 接口。"""

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """发送 HTTP 请求。

        参数 ``method``、``url`` 和关键字参数对应 HTTP 请求内容。
        返回 ``requests.Response``；底层网络错误时抛出 ``requests.RequestException``。
        """


class HTTPClient:
    """封装超时、鉴权、重试和基础请求日志的 HTTP 客户端。"""

    _RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    _IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(
        self,
        settings: ApiSettings,
        session: RequestSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """初始化 HTTP 客户端。

        参数 ``settings`` 提供基础地址、超时、重试和可选 Token；``session`` 可注入测试替身；``logger`` 可注入日志器。
        不返回值；当业务 API 方法调用 ``request`` 时复用该客户端配置。
        """

        self._settings = settings
        self._session = session or requests.Session()
        self._logger = logger or logging.getLogger(__name__)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> requests.Response:
        """发送一个 JSON HTTP 请求并按配置重试可安全重放的请求。

        参数 ``method`` 是 HTTP 方法，``path`` 是相对路径，``params`` 是查询参数，``json_body`` 是任意 JSON 请求体，
        ``headers`` 是附加请求头，``timeout_seconds`` 可覆盖默认超时。
        返回服务端的原始 ``requests.Response``，由上层 API、Service 或 Case 判断业务结果；网络错误在不可重试或重试耗尽时抛出。
        """

        url = self._build_url(path)
        normalized_method = method.upper()
        request_headers = self._build_headers(headers)
        attempts = max(self._settings.retry_attempts, 0) + 1
        for attempt in range(attempts):
            started_at = time.monotonic()
            try:
                response = self._session.request(
                    normalized_method,
                    url,
                    params=params,
                    json=json_body,
                    headers=request_headers,
                    timeout=timeout_seconds or self._settings.timeout_seconds,
                )
            except requests.RequestException as error:
                elapsed_seconds = time.monotonic() - started_at
                self._logger.warning(
                    "HTTP %s %s failed in %.3fs: %s",
                    normalized_method,
                    path,
                    elapsed_seconds,
                    type(error).__name__,
                )
                if not self._should_retry(normalized_method, attempt, attempts):
                    raise
                self._sleep_before_retry(attempt)
                continue

            elapsed_seconds = time.monotonic() - started_at
            self._logger.info("HTTP %s %s -> %s in %.3fs", normalized_method, path, response.status_code, elapsed_seconds)
            if response.status_code not in self._RETRYABLE_STATUS_CODES:
                return response
            if not self._should_retry(normalized_method, attempt, attempts):
                return response
            self._sleep_before_retry(attempt)

        raise RuntimeError("HTTP request exhausted without a response")

    def _build_url(self, path: str) -> str:
        """拼接基础地址和 API 相对路径。

        参数 ``path`` 是以斜杠开头或不带斜杠的相对路径。
        返回完整请求 URL；基础地址未配置时抛出 ``ValueError``。
        """

        if not self._settings.base_url:
            raise ValueError("API base_url is not configured")
        return f"{self._settings.base_url}/{path.lstrip('/')}"

    def _build_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        """合并默认鉴权头和调用方提供的请求头。

        参数 ``headers`` 是调用方的附加请求头。
        返回新的请求头字典；调用方显式传入的同名头优先级高于默认头。
        """

        result = {"Accept": "application/json"}
        if self._settings.auth_token:
            result["Authorization"] = f"Bearer {self._settings.auth_token}"
        if headers:
            result.update(headers)
        return result

    def _should_retry(self, method: str, attempt: int, attempts: int) -> bool:
        """判断当前失败是否允许自动重试。

        参数 ``method`` 是规范化 HTTP 方法，``attempt`` 是从零开始的当前次数，``attempts`` 是总次数。
        返回 ``True`` 表示对幂等请求仍有剩余次数并应继续重试，否则返回 ``False``。
        """

        return method in self._IDEMPOTENT_METHODS and attempt + 1 < attempts

    def _sleep_before_retry(self, attempt: int) -> None:
        """按线性退避在两次可重试请求之间等待。

        参数 ``attempt`` 是从零开始的当前失败次数。
        不返回值；当配置的退避时间大于零时暂停执行，避免立即重试。
        """

        delay = self._settings.retry_backoff_seconds * (attempt + 1)
        if delay > 0:
            time.sleep(delay)
