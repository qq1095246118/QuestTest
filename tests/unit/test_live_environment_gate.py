"""Factor 4.0 live 环境主机边界的离线单元测试。"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed

from tests.conftest import _validate_factor4_mcp_url, _validate_test_host, pytest_collection_modifyitems


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
