"""MCP 基本读取冒烟与单独标记的协议兼容专项；不执行畸形输入、限流或并发。"""

from collections.abc import Callable

import pytest

from config.settings import Settings
from service.factor4_protocol_service import Factor4ProtocolService
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        result = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert result.checked_count > 0
    assert not result.issues, ", ".join(result.issues)


@pytest.fixture(scope="module")
def protocol_service(factor4_read_service: Factor4ReadService) -> Factor4ProtocolService:
    """复用已门禁/握手客户端；正常读取不调用任何写工具。"""
    return Factor4ProtocolService(factor4_read_service.api.mcp)


def test_protocol_handshake_notification_and_read_capability(protocol_service: Factor4ProtocolService, settings: Settings) -> None:
    """MCP-001/010：协商配置版本、通知成功且声明服务器身份和工具读取能力。"""
    _verify(lambda: protocol_service.check_handshake(settings.factor_data.protocol_version))


def test_protocol_tool_inventory_has_unique_names_and_object_schemas(protocol_service: Factor4ProtocolService) -> None:
    """MCP-002/MCP-016：遍历全部页，校验目录/环境/指标/推荐/schema/universe 能力及唯一名称。"""
    _verify(protocol_service.check_descriptors)


@pytest.mark.factor4_technical
def test_protocol_new_connection_reinitializes_same_readonly_surface(protocol_service: Factor4ProtocolService, settings: Settings) -> None:
    """MCP-010 RECONNECT：独立 HTTP/MCP 连接不依赖旧 Session，握手后读取相同工具和 raw schema。"""
    _verify(lambda: protocol_service.check_reconnect(settings.factor_data.protocol_version))


@pytest.mark.factor4_technical
@pytest.mark.parametrize("tool", ["schema_get_factor_fields", "schema_get_raw_data"])
@pytest.mark.parametrize("request_id", ["questtest-schema-replay", 10401], ids=["string_id", "integer_id"])
def test_protocol_schema_serial_replay_keeps_data_and_request_correlation(protocol_service: Factor4ProtocolService, tool: str, request_id: str | int) -> None:
    """MCP-010/011：串行重复 ID 各自正确回包，schema 双表示及有效业务数据不变。"""
    _verify(lambda: protocol_service.check_schema_replay(tool, request_id))
