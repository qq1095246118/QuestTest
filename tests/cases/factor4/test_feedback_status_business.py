"""Factor 4.0 反馈状态只读权限与结果用例；不调用 submit 写工具。"""

from collections.abc import Callable

import pytest

from api.factor4_feedback_status_api import Factor4FeedbackStatusAPI
from config.settings import Settings
from db.client import DatabaseClient
from db.factor4_feedback_repository import Factor4FeedbackRepository
from service.factor4_feedback_status_service import Factor4FeedbackStatusService, FeedbackCaller
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


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
def status_service(factor4_read_service: Factor4ReadService, settings: Settings) -> Factor4FeedbackStatusService:
    """门禁成功后创建只读反馈 API/Repository；DB/协议异常不吞掉。"""
    return Factor4FeedbackStatusService(Factor4FeedbackStatusAPI(factor4_read_service.api.mcp), Factor4FeedbackRepository(DatabaseClient.from_settings(settings.database)))


@pytest.fixture(scope="module")
def feedback_caller(status_service: Factor4FeedbackStatusService) -> FeedbackCaller:
    """按本轮精确审计请求定位当前 MCP 主体；不能复用其他调用最近日志。"""
    try:
        return status_service.discover_caller()
    except ReadContractError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.mark.factor4_technical
def test_feedback_current_request_maps_to_active_key_owner_and_granted_scope(status_service: Factor4FeedbackStatusService, feedback_caller: FeedbackCaller) -> None:
    """DB-608：本轮 key/user/required_scope 审计一致，不假装验证未配置普通浏览账号。"""
    _verify(lambda: status_service.check_caller_scope(feedback_caller))


def test_feedback_owned_submission_status_counters_and_error_match_database(status_service: Factor4FeedbackStatusService, feedback_caller: FeedbackCaller) -> None:
    """FB-OWNED：该 token 主体真实 owned 提交状态和计数逐字段 DB 对账。"""
    _verify(lambda: status_service.check_owned(feedback_caller))


@pytest.mark.parametrize("other_owner", [
    pytest.param(False, id="missing"),
    pytest.param(True, id="other_owner", marks=pytest.mark.factor4_technical),
])
def test_feedback_missing_and_other_owner_ids_do_not_reveal_submission_data(status_service: Factor4FeedbackStatusService, feedback_caller: FeedbackCaller, other_owner: bool) -> None:
    """FB-RANDOM/OTHER：已确认不存在 ID 和其他主体真实 ID 均 NOT_FOUND 且不泄漏状态。"""
    _verify(lambda: status_service.check_unavailable(feedback_caller, other_owner=other_owner))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("arguments", [
    {"submission_id": 7}, {"submission_id": 7.5}, {"submission_id": True},
    {"submission_id": "   "}, {"submission_id": "\t\n"}, {"submission_id": "x" * 129},
    {"submission_id": "x" * 4096}, {"submission_id": "不存在提交-questtest-protocol-negative"},
    {"submission_id": "' OR 1=1 --"}, {"submission_id": None}, {},
    {"submission_id": ["x"]}, {"submission_id": {"id": "x"}},
], ids=["integer", "float", "boolean", "blank", "whitespace", "overlong", "extreme_length", "unicode_missing", "sql_like", "null", "missing", "array", "object"])
def test_feedback_invalid_or_nonexistent_identifiers_are_rejected_without_leak(status_service: Factor4FeedbackStatusService, arguments: dict[str, object]) -> None:
    """FB-MATRIX：迁移完整异常输入矩阵；默认门禁不执行，但每项有真实请求和拒绝/无泄漏断言。"""
    _verify(lambda: status_service.check_invalid_arguments(arguments))
