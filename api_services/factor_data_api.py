from __future__ import annotations

from typing import Any

from config.settings import settings
from infrastructure.http.http_client import HTTPClient


class FactorDataAPI:
    def __init__(self):
        self.base_url = settings.base_url
        self.headers = {"Content-Type": "application/json"}

    def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        data=None,
        params: dict[str, Any] | None = None,
    ):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request(
            "POST",
            url,
            headers=self.headers,
            json=json,
            data=data,
            params=_clean(params),
        )

    def query(
        self,
        dataset: Any = None,
        symbols: Any = None,
        interval: Any = None,
        start_time_ms: Any = None,
        end_time_ms: Any = None,
        fields: Any = None,
        quality_flags: Any = None,
        page_size: Any = None,
        cursor: Any = None,
        sort: Any = None,
        include_symbol_coverage: Any = None,
    ):
        return self.post(
            "/api/v1/factor-data/query",
            json=_clean(
                {
                    "dataset": dataset,
                    "symbols": symbols,
                    "interval": interval,
                    "start_time_ms": start_time_ms,
                    "end_time_ms": end_time_ms,
                    "fields": fields,
                    "quality_flags": quality_flags,
                    "page_size": page_size,
                    "cursor": cursor,
                    "sort": sort,
                    "include_symbol_coverage": include_symbol_coverage,
                }
            ),
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if value is not None}
