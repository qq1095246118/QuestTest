from __future__ import annotations

from types import SimpleNamespace

from api.platform.auth_api import AuthAPI


def test_login_uses_configured_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"] = method
        calls["url"] = url
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://example.test")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_email", "user@example.test")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_password", "dummy-password")

    response = AuthAPI().login()

    assert response.status_code == 200
    assert calls["method"] == "POST"
    assert calls["url"] == "https://example.test/api/v1/auth/login"
    assert calls["kwargs"]["headers"] == {"Content-Type": "application/json"}
    assert calls["kwargs"]["json"] == {"email": "user@example.test", "password": "dummy-password"}


def test_login_accepts_explicit_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://example.test")

    AuthAPI().login(email="explicit@example.test", password="explicit-password")

    assert calls["kwargs"]["json"] == {"email": "explicit@example.test", "password": "explicit-password"}


def test_login_preserves_explicit_empty_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://example.test")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_email", "user@example.test")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_password", "dummy-password")

    AuthAPI().login(email="", password="")

    assert calls["kwargs"]["json"] == {"email": "", "password": ""}
