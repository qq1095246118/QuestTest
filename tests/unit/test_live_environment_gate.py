"""Factor 4.0 live 环境主机边界的离线单元测试。"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest
from _pytest.outcomes import Failed, Skipped

from config.settings import Settings, SettingsLoader
from tests.conftest import (
    _validate_factor4_backend_url,
    _validate_factor4_calculation_live_environment,
    _validate_factor4_host,
    _validate_factor4_mcp_url,
    _validate_factor_combo_live_environment,
    _validate_test_host,
    factor4_backend_api_settings,
    pytest_collection_modifyitems,
)


pytestmark = pytest.mark.unit


def test_deferred_factor4_cases_are_skipped_before_fixtures_by_default() -> None:
    """Execution scope cannot be broadened just by running the Factor 4.0 directory."""
    from unittest.mock import Mock
    config = Mock()
    config.getoption.return_value = False
    deferred, ordinary = Mock(), Mock()
    deferred.get_closest_marker.side_effect = lambda name: pytest.mark.factor4_deferred if name == "factor4_deferred" else None
    ordinary.get_closest_marker.return_value = None
    pytest_collection_modifyitems(config, [deferred, ordinary])
    assert deferred.add_marker.call_args.args[0].name == "skip"
    ordinary.add_marker.assert_not_called()
    deferred.reset_mock()
    config.getoption.return_value = True
    pytest_collection_modifyitems(config, [deferred])
    deferred.add_marker.assert_not_called()


def test_validate_test_host_accepts_case_and_trailing_dot_variants() -> None:
    """DNS 主机名的大小写和末尾根点不应绕过已登记的测试主机。"""

    _validate_test_host(
        "TEST-FACTOR-FRONTEND.QUESTVECTOR.AI.",
        ("test-factor-frontend.questvector.ai",),
        "MCP",
    )


@pytest.mark.parametrize(
    ("host", "allowlist", "message"),
    [
        (
            "factor-frontend.questvector.ai",
            ("factor-frontend.questvector.ai",),
            "生产",
        ),
        (
            "unlisted.example.test",
            ("test-factor-frontend.questvector.ai",),
            "不在测试环境白名单",
        ),
        (
            "test-factor-frontend.questvector.ai",
            (),
            "未配置测试环境主机白名单",
        ),
    ],
)
def test_validate_test_host_rejects_unsafe_hosts(
    host: str,
    allowlist: tuple[str, ...],
    message: str,
) -> None:
    """生产、未登记和空白 allowlist 都必须在请求前终止 live 测试。"""

    with pytest.raises(Failed, match=message):
        _validate_test_host(host, allowlist, "MCP")


def test_validate_test_host_rejects_production_domain_in_allowlist() -> None:
    """即使当前请求是测试域名，allowlist 本身也不能含生产域名。"""

    with pytest.raises(Failed, match="白名单包含生产域名"):
        _validate_test_host(
            "test-factor-frontend.questvector.ai",
            (
                "test-factor-frontend.questvector.ai",
                "factor-frontend.questvector.ai",
            ),
            "MCP",
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@test-factor-frontend.questvector.ai/mcp/factor-data",
        "https://user@test-factor-frontend.questvector.ai/mcp/factor-data",
        "https://:password@test-factor-frontend.questvector.ai/mcp/factor-data",
    ],
)
def test_factor4_mcp_url_rejects_userinfo(url: str) -> None:
    """MCP 地址中的用户名/密码不能进入测试连接配置。"""

    with pytest.raises(Failed, match="不得包含用户信息"):
        _validate_factor4_mcp_url(
            url,
            ("test-factor-frontend.questvector.ai",),
        )


def test_factor4_mcp_url_accepts_only_the_declared_endpoint() -> None:
    """合法测试地址返回已解析结果，并拒绝附加查询参数。"""

    parsed = _validate_factor4_mcp_url(
        "https://test-factor-frontend.questvector.ai/mcp/factor-data/",
        ("test-factor-frontend.questvector.ai",),
    )
    assert parsed.hostname == "test-factor-frontend.questvector.ai"

    with pytest.raises(Failed, match="精确指向"):
        _validate_factor4_mcp_url(
            "https://test-factor-frontend.questvector.ai/mcp/factor-data?debug=1",
            ("test-factor-frontend.questvector.ai",),
        )


@pytest.fixture
def production_settings() -> Settings:
    """返回完全合成的 production 配置；无文件或外部访问，转换失败透传。"""
    return SettingsLoader._to_settings({
        "environment": "production", "api": {}, "authentication": {}, "database": {},
        "factor_combo": {}, "factor_data": {}, "reports": {},
        "environment_safety": {"allowed_hosts": ["factor-backend.questvector.ai"]},
    })


def test_production_requires_explicit_matching_environment_and_readonly(production_settings: Settings) -> None:
    """显式 production+live 才放行，默认环境、错配或关闭只读都必须阻断。"""
    config = Mock()
    config.getoption.side_effect = {"--live": True, "--env": "production"}.__getitem__
    _validate_factor4_calculation_live_environment(production_settings, config)
    with pytest.raises(Failed, match="read_only"):
        _validate_factor4_calculation_live_environment(
            replace(production_settings, database=replace(production_settings.database, read_only=False)), config,
        )
    config.getoption.side_effect = {"--live": True, "--env": "test"}.__getitem__
    with pytest.raises(Failed, match="不一致"):
        _validate_factor4_calculation_live_environment(production_settings, config)
    config.getoption.side_effect = {"--live": True, "--env": None}.__getitem__
    with pytest.raises(Failed, match="显式传入"):
        _validate_factor4_calculation_live_environment(production_settings, config)
    config.getoption.side_effect = {"--live": False, "--env": "production"}.__getitem__
    with pytest.raises(Skipped, match="BLOCKED_ENV"):
        _validate_factor4_calculation_live_environment(production_settings, config)


def test_combo_production_remains_blocked(production_settings: Settings) -> None:
    """Factor 4.0 放行不允许组合因子台在生产执行写入链路。"""
    with pytest.raises(Failed, match="只允许 --env test"):
        _validate_factor_combo_live_environment(production_settings, True)


def test_production_mcp_accepts_only_registered_non_test_host() -> None:
    """生产 URL 仍受精确端点、独立主机及 test/production 隔离约束。"""
    host = "factor-frontend.questvector.ai"
    assert _validate_factor4_mcp_url(
        f"https://{host}/mcp/factor-data", (host,), environment="production",
    ).hostname == host
    with pytest.raises(Failed, match="production 白名单"):
        _validate_factor4_mcp_url(
            f"https://{host}/mcp/factor-data", ("other.example.org",), environment="production",
        )
    with pytest.raises(Failed, match="混入测试环境"):
        _validate_factor4_mcp_url(
            f"https://{host}/mcp/factor-data", (host, "test-factor-frontend.questvector.ai"),
            environment="production",
        )
    with pytest.raises(Failed, match="精确指向"):
        _validate_factor4_mcp_url(
            f"https://{host}/mcp/factor-data?debug=1", (host,), environment="production",
        )
    with pytest.raises(Failed, match="混入测试环境"):
        _validate_factor4_host("43.167.190.122", ("43.167.190.122",), "DB", environment="production")


@pytest.mark.parametrize("path", ["/api/v1?debug=1", "/api/v2", "/api/v1#debug", "/prefix/api/v1"])
def test_production_backend_keeps_exact_path_contract(path: str) -> None:
    """新增 Backend 入口仍阻断错误路径和附加参数，不发送认证请求。"""
    with pytest.raises(Failed, match="精确指向"):
        _validate_factor4_backend_url(f"https://factor-backend.questvector.ai{path}", ("factor-backend.questvector.ai",))


@pytest.mark.parametrize("has_url,has_token", [(False, False), (True, False), (False, True)])
def test_missing_production_backend_config_skips_without_test_login(
    production_settings: Settings, has_url: bool, has_token: bool,
) -> None:
    """缺生产 Backend 地址或独立凭据时先跳过，绝不调用旧测试认证 fixture。"""
    selected = replace(production_settings, api=replace(
        production_settings.api,
        base_url="https://factor-backend.questvector.ai/api/v1" if has_url else "",
        auth_token="synthetic-backend-token" if has_token else None,
    ))
    config, request = Mock(), Mock()
    config.getoption.side_effect = {"--live": True, "--env": "production"}.__getitem__
    with pytest.raises(Skipped, match="BLOCKED_ENV: production Backend"):
        factor4_backend_api_settings.__wrapped__(selected, config, request)
    request.getfixturevalue.assert_not_called()


def test_production_backend_authenticates_only_its_own_token(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整生产配置只传入自身 JWT 认证，不复用测试账号或 MCP Token。"""
    selected = replace(production_settings, api=replace(
        production_settings.api,
        base_url="https://factor-backend.questvector.ai/api/v1", auth_token="synthetic-backend-token",
    ))
    config, request, authenticate = Mock(), Mock(), Mock()
    config.getoption.side_effect = {"--live": True, "--env": "production"}.__getitem__
    authenticate.return_value.api_settings = selected.api
    monkeypatch.setattr("tests.conftest._authenticate_account", authenticate)
    assert factor4_backend_api_settings.__wrapped__(selected, config, request) == selected.api
    assert authenticate.call_args.args[0] == selected.api
    assert authenticate.call_args.args[1] is None
    request.getfixturevalue.assert_not_called()
