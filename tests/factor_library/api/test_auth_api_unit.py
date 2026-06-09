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
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://test-factor-backend.questvector.ai")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_email", "haoran@gmail.com")
    monkeypatch.setattr("api.platform.auth_api.settings.factor_password", "Aa%@#haoran")

    response = AuthAPI().login()

    assert response.status_code == 200
    assert calls["method"] == "POST"
    assert calls["url"] == "https://test-factor-backend.questvector.ai/api/v1/auth/login"
    assert calls["kwargs"]["headers"] == {"Content-Type": "application/json"}
    assert calls["kwargs"]["json"] == {"email": "haoran@gmail.com", "password": "Aa%@#haoran"}


def test_login_accepts_explicit_credentials(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "data": {"token": "abc"}})

    monkeypatch.setattr("api.platform.auth_api.HTTPClient.request", fake_request)
    monkeypatch.setattr("api.platform.auth_api.settings.base_url", "https://test-factor-backend.questvector.ai")

    AuthAPI().login(email="user@example.com", password="secret")

    assert calls["kwargs"]["json"] == {"email": "user@example.com", "password": "secret"}
