"""完整实现但遵守用户范围默认不执行的异常/兼容/并发 Case；没有占位断言。"""

from collections.abc import Callable
import json

import pytest

from config.settings import Settings
from db.client import DatabaseClient
from db.factor4_auxiliary_repository import Factor4AuxiliaryRepository
from service.factor4_protocol_boundary_service import READ_TOOLS, Factor4ProtocolBoundaryService, check_explicit_rejection, check_strict_dual_representation
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition, read_tool_page

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation, pytest.mark.factor4_deferred]


def _verify(action: Callable[[], ReadCheck]) -> None:
    try:
        check = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)
    assert check.checked_count > 0
    assert not check.issues, ", ".join(check.issues)


@pytest.fixture(scope="module")
def boundary_service(factor4_read_service: Factor4ReadService, settings: Settings) -> Factor4ProtocolBoundaryService:
    """显式 deferred/live/test 门禁成功后提供只读边界服务；无业务写入口。"""
    return Factor4ProtocolBoundaryService(factor4_read_service.api.mcp, Factor4AuxiliaryRepository(DatabaseClient.from_settings(settings.database)))


@pytest.mark.parametrize("raw,code", [
    (b'{"jsonrpc":"2.0","id":1,"method":"tools/list"', -32700),
    (b'[]', -32600), (b'{"id":1,"method":"tools/list","params":{}}', -32600),
    (b'{"jsonrpc":"1.0","id":1,"method":"tools/list","params":{}}', -32600),
    (b'{"jsonrpc":"2.0","id":1,"method":"questtest/unknown","params":{}}', -32601),
    (b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}', -32602),
    (b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"factor_search","arguments":[]}}', -32602),
], ids=["malformed_json", "array_root", "missing_version", "wrong_version", "unknown_method", "missing_params", "wrong_arguments_container"])
def test_raw_jsonrpc_rejects_exact_protocol_error(boundary_service: Factor4ProtocolBoundaryService, raw: bytes, code: int) -> None:
    """MCP-011：精确错误码和JSONRPC信封，不能把任意拒绝/解析失败当成功。"""
    _verify(lambda: boundary_service.check_raw_rejection(raw, code))


@pytest.mark.parametrize("tool", [tool for tool in READ_TOOLS if tool != "get_feedback_submission_status"])
def test_protocol_schema_legal_and_invalid_boundaries(boundary_service: Factor4ProtocolBoundaryService, tool: str) -> None:
    """MCP-006：实时schema所有read工具的baseline、额外字段、枚举、limit合法/非法边界。"""
    _verify(lambda: boundary_service.check_schema_boundaries(tool))


@pytest.mark.parametrize("accept", ["application/json", "text/event-stream", "application/json, text/event-stream"])
def test_protocol_json_and_sse_accept_negotiation(boundary_service: Factor4ProtocolBoundaryService, accept: str) -> None:
    """MCP-014：每种 Accept 得到可完整解析且相同业务数据。"""
    _verify(lambda: boundary_service.check_accept(accept))


@pytest.mark.parametrize("tool,workers", [("schema_get_factor_fields", 3), ("environment_get_daily", 6), ("factor_search", 6)])
def test_fixed_snapshot_parallel_reads_return_identical_business_data(boundary_service: Factor4ProtocolBoundaryService, tool: str, workers: int) -> None:
    """MCP-015：独立会话固定输入前中后重放一致，不称性能验收。"""
    _verify(lambda: boundary_service.check_concurrent(tool, workers))


def test_unknown_protocol_version_is_rejected_or_explicitly_negotiated(boundary_service: Factor4ProtocolBoundaryService, settings: Settings) -> None:
    """MCP-011：未知版本不可原样接受为受支持版本。"""
    _verify(lambda: boundary_service.check_unknown_version(settings.factor_data.protocol_version))


@pytest.mark.parametrize("mode", ["search_tamper", "search_kind", "search_filter", "search_limit", "search_replay", "search_cross_tool", "children_tamper", "children_parent", "children_limit", "children_replay"])
def test_catalog_cursor_scope_binding_and_tamper_rejection(boundary_service: Factor4ProtocolBoundaryService, mode: str) -> None:
    """目录与children分页游标绑定查询条件并支持正常重放，篡改仅接受显式参数拒绝。"""
    _verify(lambda: boundary_service.check_cursor(mode))


@pytest.mark.parametrize("tool", ["schema_get_factor_fields", "schema_get_raw_data", "environment_get_daily"])
def test_tool_text_json_and_structured_content_are_complete_and_identical(boundary_service: Factor4ProtocolBoundaryService, tool: str) -> None:
    """MCP-014严格双表示：不对截断text回退structured；缺失、不可解析和正文不一致均失败。"""
    response = boundary_service.api.call_tool(tool, boundary_service.arguments(tool))
    _verify(lambda: check_strict_dual_representation(response))


@pytest.mark.parametrize("tool,arguments,codes", [
    ("environment_get_daily", {"environment_date": "2026-02-30"}, ("INVALID_ARGUMENT",)),
    ("environment_get_daily", {"as_of": "not-a-date"}, ("INVALID_ARGUMENT",)),
    ("universe_list_symbols", {"universe_key": "__questtest_unknown_universe__"}, ("UNIVERSE_NOT_FOUND",)),
    ("factor_search", {"updated_after": "not-a-date"}, ("INVALID_ARGUMENT",)),
    ("factor_search", {"as_of": "not-a-date"}, ("INVALID_ARGUMENT",)),
    ("kb_factor_candidate_search", {"extraction_id": -1}, ("INVALID_ARGUMENT",)),
    ("factor_search", {"limit": 1.5}, ("INVALID_ARGUMENT",)),
    ("factor_search", {"tags": "momentum"}, ("INVALID_ARGUMENT",)),
    ("factor_search", {"library_status": "valid", "as_of": "2026-01-01T00:00:00Z"}, ("INVALID_ARGUMENT",)),
    ("factor_get_detail", {"factor_ref": "factor:0"}, ("INVALID_ARGUMENT",)),
    ("factor_get_detail", {"factor_ref": "sub_factor:-1"}, ("INVALID_ARGUMENT",)),
    ("factor_get_details_batch", {"factor_refs": "factor:1"}, ("INVALID_ARGUMENT",)),
    ("factor_get_details_batch", {"factor_refs": [1]}, ("INVALID_ARGUMENT",)),
    ("factor_get_details_batch", {"factor_refs": ["bad-ref"]}, ("INVALID_ARGUMENT",)),
    ("factor_get_details_batch", {"factor_refs": []}, ("INVALID_ARGUMENT",)),
    ("factor_get_details_batch", {"factor_refs": ["factor:1"]*51}, ("INVALID_ARGUMENT",)),
    ("kb_factor_candidate_search", {"extraction_id": 0}, ("INVALID_ARGUMENT",)),
    ("kb_factor_candidate_search", {"validation_status": "verified"}, ("INVALID_ARGUMENT",)),
    ("kb_factor_candidate_search", {"query": "btc", "min_confidence": -0.01}, ("INVALID_ARGUMENT",)),
    ("universe_list_symbols", {"universe_key": ""}, ("INVALID_ARGUMENT", "UNIVERSE_NOT_FOUND")),
    ("universe_list_symbols", {"universe_key": None}, ("INVALID_ARGUMENT",)),
])
def test_semantically_invalid_read_arguments_return_explicit_errors(boundary_service: Factor4ProtocolBoundaryService, tool: str, arguments: dict[str, object], codes: tuple[str, ...]) -> None:
    """日期/自然ID/unknown universe 的业务错误，不允许内部错误或HTTP失败混充验收通过。"""
    _verify(lambda: check_explicit_rejection(boundary_service.api, tool, arguments, codes=codes))


def test_mixed_batch_keeps_existing_positions_and_isolates_missing_ref(boundary_service: Factor4ProtocolBoundaryService) -> None:
    """DETAIL-020：不存在ref只影响自身位置，前后重复合法ref仍成功。"""
    _verify(boundary_service.check_mixed_batch)


@pytest.mark.parametrize("mode", ["omit", "invalid"])
def test_stateful_session_id_cannot_be_omitted_or_forged(boundary_service: Factor4ProtocolBoundaryService, mode: str) -> None:
    """MCP-010：仅stateful服务校验session绑定；无Session为明确不可适用而非成功。"""
    _verify(lambda: boundary_service.check_session_binding(mode))


def test_feedback_write_tool_schema_is_inspected_without_invocation(boundary_service: Factor4ProtocolBoundaryService) -> None:
    """MCP-006写工具只验证声明，任何运行都不提交反馈。"""
    _verify(boundary_service.check_write_schema_only)


def test_unknown_kb_candidate_returns_successful_empty_result(boundary_service: Factor4ProtocolBoundaryService) -> None:
    """KB-005：合法但确定不存在候选ID返回空页，不把未知候选当协议错误。"""
    missing = boundary_service.repository.absent_candidate_id()
    page = read_tool_page(boundary_service.api.call_tool("kb_factor_candidate_search", {"extraction_id": missing}))
    assert not page.items


@pytest.mark.parametrize("kind", ["fact", "forecast"])
def test_large_daily_pages_exhaust_exact_database_rows_without_truncation(boundary_service: Factor4ProtocolBoundaryService, factor4_read_service: Factor4ReadService, kind: str) -> None:
    """MCP-014：1000行最大页及后续页全部DB对账；不把schema响应尺寸/耗时当业务正确性。"""
    snapshot = boundary_service.repository.daily_snapshot()
    traversal = factor4_read_service.daily_pages(snapshot, kind, page_size=1000)
    _verify(lambda: factor4_read_service.check_daily(snapshot, kind, traversal))


@pytest.mark.parametrize("mode,method", [("missing", "initialize"), ("invalid", "initialize"), ("malformed", "initialize"), ("missing", "tools/call")])
def test_missing_invalid_and_malformed_bearer_never_access_read_tools(boundary_service: Factor4ProtocolBoundaryService, mode: str, method: str) -> None:
    """SEC-AUTH：握手和未授权factor_search均必须明确401/403，不能泄漏catalog业务数据。"""
    _verify(lambda: boundary_service.check_authentication(mode, method))


def test_untrusted_origin_is_rejected_without_tool_result(boundary_service: Factor4ProtocolBoundaryService) -> None:
    """MCP origin安全子项，错误Origin不能访问工具目录；默认deferred不请求。"""
    _verify(boundary_service.check_untrusted_origin)


def test_protocol_responses_never_echo_configured_authentication_secret(boundary_service: Factor4ProtocolBoundaryService, settings: Settings) -> None:
    """SEC-ECHO：只比较内存中的真实认证token，不在失败消息、日志、JUnit中打印它。"""
    _verify(lambda: boundary_service.check_credential_echo(settings.factor_data.auth_token))
