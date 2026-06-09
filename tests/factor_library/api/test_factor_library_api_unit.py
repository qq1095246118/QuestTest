from __future__ import annotations

from types import SimpleNamespace

from api.platform.factor_library_api import FactorLibraryAPI


def test_list_factors_sends_clean_query_params(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"] = method
        calls["url"] = url
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True})

    monkeypatch.setattr("api.platform.factor_library_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.factor_library_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    FactorLibraryAPI(token="token-1").list_factors(
        page=1,
        limit=5,
        factor_theme="sentiment",
        status=None,
        sort_by="updated_at",
        sort_order="asc",
    )

    assert calls["method"] == "GET"
    assert calls["url"] == "https://test-factor-backend.questvector.ai/api/v1/factors"
    assert calls["kwargs"]["headers"]["Authorization"] == "Bearer token-1"
    assert calls["kwargs"]["params"] == {
        "page": 1,
        "limit": 5,
        "factor_theme": "sentiment",
        "sort_by": "updated_at",
        "sort_order": "asc",
    }


def test_factor_library_auxiliary_routes(monkeypatch):
    urls = []

    def fake_request(method, url, **kwargs):
        urls.append((method, url, kwargs.get("params")))
        return SimpleNamespace(status_code=200, json=lambda: {"success": True})

    monkeypatch.setattr("api.platform.factor_library_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.factor_library_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    api = FactorLibraryAPI(token="token-1")
    api.list_themes()
    api.list_factor_theme_tree()
    api.list_sub_factors(factor_id=615)
    api.get_factor_ic_summary(factor_id=615, ic_scope="time_series", time_window="1h")

    assert urls == [
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/themes", {}),
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/factors/theme-tree", {}),
        ("GET", "https://test-factor-backend.questvector.ai/api/v1/sub-factors", {"factor_id": 615}),
        (
            "GET",
            "https://test-factor-backend.questvector.ai/api/v1/factor-ic/factors/615/summary",
            {"ic_scope": "time_series", "time_window": "1h"},
        ),
    ]
