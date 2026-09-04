"""pytest 全局 Fixture、环境选择和测试标记注册。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

import pytest

from api.agent_api import AgentAPI
from api.auth_api import AuthAPI, AuthResponsePayload, AuthenticatedAccount
from api.chat_api import ChatAPI
from api.client import HTTPClient
from api.factor_combo_api import FactorComboAPI
from api.performance_api import PerformanceAPI
from api.sub_factor_api import SubFactorAPI
from config.settings import AccountCredentials, ApiSettings, Settings, SettingsLoader
from db.client import DatabaseClient
from db.factor_combo_repository import FactorComboRepository
from service.factor_combo_service import FactorComboService
from tests.resource_scope import TestResourceScope
from tools.http_response import read_json_object, read_json_or_diagnostic


def pytest_addoption(parser: pytest.Parser) -> None:
    """注册测试运行时的环境选择参数。

    参数 ``parser`` 是 pytest 提供的命令行参数解析器。
    不返回值；新增 ``--env`` 参数，默认使用 ``test`` 环境配置。
    """

    parser.addoption(
        "--env",
        action="store",
        default=None,
        help="Environment configuration name under config/, for example test or staging.",
    )
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Enable tests that call a configured external API or database.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """向 pytest 注册框架支持的测试标记。

    参数 ``config`` 是当前 pytest 配置对象。
    不返回值；使未在 pyproject 中声明时的标记也能在 pytest 运行中被识别。
    """

    config.addinivalue_line("markers", "smoke: core checks that do not require a real environment")
    config.addinivalue_line("markers", "regression: repeatable regression coverage")
    config.addinivalue_line("markers", "integration: cases that require a configured external API or database")
    config.addinivalue_line("markers", "worker_contract: cases that call test-only Worker compatibility endpoints")
    config.addinivalue_line("markers", "external_agent: cases that start or poll a real research Agent run")
    config.addinivalue_line("markers", "unit: offline framework and service/repository unit tests")


@pytest.fixture(scope="session")
def settings(pytestconfig: pytest.Config) -> Settings:
    """加载当前 pytest 命令选择的环境配置。

    参数 ``pytestconfig`` 提供 ``--env`` 命令行选项。
    返回一个会话级 ``Settings``；配置错误时测试在执行前失败。
    """

    environment = pytestconfig.getoption("--env")
    return SettingsLoader.load(environment=str(environment) if environment else None)


@pytest.fixture(scope="session")
def live_mode(pytestconfig: pytest.Config) -> bool:
    """判断本次运行是否显式允许真实环境测试。

    参数 ``pytestconfig`` 提供 ``--live`` 命令行选项。
    返回布尔值；命令行开关或 ``AUTOMATION_LIVE=true`` 时返回 ``True``，否则返回 ``False``。
    """

    import os

    configured = os.getenv("AUTOMATION_LIVE", "").strip().lower() == "true"
    return bool(pytestconfig.getoption("--live") or configured)


@pytest.fixture(scope="session")
def privileged_account(settings: Settings, live_mode: bool) -> AuthenticatedAccount:
    """登录并校验有权限账号，返回可跨 Factor/Agent API 复用的完整上下文。

    参数 ``settings`` 提供地址和账号配置，``live_mode`` 控制真实访问开关。
    返回 ``AuthenticatedAccount``；账号必须 approved 且拥有 ``use_factor_agent``、``use_research_agent`` 和
    ``manage_factor_library``，否则直接失败。
    """

    _validate_factor_combo_live_environment(settings, live_mode)
    credentials = settings.authentication.privileged
    if credentials.email and credentials.password:
        return _authenticate_account(
            settings.api,
            credentials,
            "有权限",
            required_permissions={"use_factor_agent", "use_research_agent", "manage_factor_library"},
        )
    if credentials.email or credentials.password:
        pytest.fail("有权限账号必须同时配置 AUTOMATION_PRIVILEGED_EMAIL 和 AUTOMATION_PRIVILEGED_PASSWORD")
    if settings.api.auth_token:
        return _authenticate_account(
            settings.api,
            None,
            "有权限 Token",
            required_permissions={"use_factor_agent", "use_research_agent", "manage_factor_library"},
        )
    pytest.skip("需要配置有权限账号密码；临时调试也可配置 AUTOMATION_API_AUTH_TOKEN")


@pytest.fixture(scope="session")
def privileged_api_settings(privileged_account: AuthenticatedAccount) -> ApiSettings:
    """兼容旧用例，返回已通过权限校验的有权限账号 API 配置。

    参数 ``privileged_account`` 是完整认证上下文。
    返回其 ``ApiSettings``；用户 ID 和权限信息请使用 ``privileged_account``，不要重新解析 Token。
    """

    return privileged_account.api_settings


@pytest.fixture(scope="session")
def restricted_account(settings: Settings, live_mode: bool) -> AuthenticatedAccount:
    """登录无权限账号并生成携带动态 JWT 的 API 配置。

    参数 ``settings`` 提供基础网络配置和无权限账号，``live_mode`` 控制是否允许访问测试环境。
    返回完整的受限 ``AuthenticatedAccount``；账号不得拥有组合实验或报告登记权限，配置缺失时跳过，配置不完整、
    登录失败或权限前置不成立时直接失败。账号可能拥有与本批接口无关的其他权限，不将其误判为配置错误。
    """

    _validate_factor_combo_live_environment(settings, live_mode)
    credentials = settings.authentication.restricted
    if credentials.email and credentials.password:
        account = _authenticate_account(settings.api, credentials, "无权限", required_permissions=set())
        unexpected_permissions = sorted({"use_factor_agent", "use_research_agent"} & account.permissions)
        if unexpected_permissions:
            raise RuntimeError(f"无权限账号意外拥有受测权限: {', '.join(unexpected_permissions)}")
        return account
    if credentials.email or credentials.password:
        pytest.fail("无权限账号必须同时配置 AUTOMATION_RESTRICTED_EMAIL 和 AUTOMATION_RESTRICTED_PASSWORD")
    pytest.skip("无权限场景需要配置 AUTOMATION_RESTRICTED_EMAIL 和 AUTOMATION_RESTRICTED_PASSWORD")


@pytest.fixture(scope="session")
def restricted_api_settings(restricted_account: AuthenticatedAccount) -> ApiSettings:
    """兼容旧用例，返回已登录无权限账号的 API 配置。

    参数 ``restricted_account`` 是完整认证上下文。
    返回其 ``ApiSettings``；需要用户 ID 时使用 ``restricted_account.user_id``。
    """

    return restricted_account.api_settings


@pytest.fixture(scope="session")
def non_owner_account(
    settings: Settings,
    live_mode: bool,
    privileged_account: AuthenticatedAccount,
) -> AuthenticatedAccount:
    """登录并校验一个具备业务权限但不拥有当前测试资源的账号。

    参数 ``settings`` 提供独立非所有者账号配置，``live_mode`` 控制真实访问开关，``privileged_account`` 用于确认
    账号身份确实不同。返回 ``AuthenticatedAccount``；未配置独立账号时跳过所有权隔离场景，配置不完整、登录失败、
    账号身份重复或权限不足时直接失败，避免把 403 误当成 404 所有权覆盖。
    """

    _validate_factor_combo_live_environment(settings, live_mode)
    credentials = settings.authentication.non_owner
    if not credentials.email and not credentials.password:
        pytest.skip(
            "所有权隔离场景需要配置 AUTOMATION_NON_OWNER_EMAIL 和 AUTOMATION_NON_OWNER_PASSWORD"
        )
    if not credentials.email or not credentials.password:
        pytest.fail("非所有者账号必须同时配置 AUTOMATION_NON_OWNER_EMAIL 和 AUTOMATION_NON_OWNER_PASSWORD")
    account = _authenticate_account(
        settings.api,
        credentials,
        "非所有者",
        required_permissions={"use_factor_agent", "use_research_agent", "manage_factor_library"},
    )
    if account.user_id == privileged_account.user_id or account.email.casefold() == privileged_account.email.casefold():
        pytest.fail("非所有者账号不能与有权限账号是同一用户")
    return account


@pytest.fixture(scope="session")
def non_owner_api_settings(non_owner_account: AuthenticatedAccount) -> ApiSettings:
    """返回已校验非所有者账号的 API 配置。

    参数 ``non_owner_account`` 是独立登录并通过权限校验的账号上下文。返回其 JWT API 设置，不执行额外请求。
    """

    return non_owner_account.api_settings


@pytest.fixture(scope="session")
def factor_combo_api(privileged_api_settings: ApiSettings) -> FactorComboAPI:
    """创建组合因子真实接口客户端。

    参数 ``privileged_api_settings`` 包含有权限账号登录后取得的 JWT。
    返回带动态鉴权的 ``FactorComboAPI``；认证前置失败时不会创建客户端。
    """

    return FactorComboAPI(HTTPClient(privileged_api_settings))


@pytest.fixture(scope="session")
def factor_combo_performance_api(privileged_api_settings: ApiSettings) -> PerformanceAPI:
    """创建有权限账号的 Performance Refresh 查询客户端。

    参数 ``privileged_api_settings`` 是完成登录和权限校验的 Factor API 配置。
    返回只包含刷新任务 GET 能力的客户端，不提供任务创建方法。
    """

    return PerformanceAPI(HTTPClient(privileged_api_settings))


@pytest.fixture(scope="session")
def factor_combo_sub_factor_api(privileged_api_settings: ApiSettings) -> SubFactorAPI:
    """创建有权限账号的登记后子因子查询客户端。

    参数 ``privileged_api_settings`` 是完成登录和权限校验的 Factor API 配置。
    返回子因子详情协议客户端。
    """

    return SubFactorAPI(HTTPClient(privileged_api_settings))


@pytest.fixture(scope="session")
def factor_combo_agent_api(
    settings: Settings,
    privileged_account: AuthenticatedAccount,
) -> AgentAPI:
    """创建携带用户归属头的 Agent API 客户端。

    参数 ``settings`` 提供 Agent API 地址，``privileged_account`` 提供同一用户的 JWT。
    返回 Agent 协议客户端；Agent 地址未配置时跳过，避免普通 Worker 合约测试被无关前置条件阻断。
    """

    if not settings.factor_combo.agent_base_url:
        pytest.skip("真实 Agent 测试需要配置 AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL")
    return AgentAPI(
        HTTPClient(replace(privileged_account.api_settings, base_url=settings.factor_combo.agent_base_url.rstrip("/")))
    )


@pytest.fixture(scope="session")
def factor_combo_restricted_api(restricted_api_settings: ApiSettings) -> FactorComboAPI:
    """创建已登录但无业务权限的组合因子客户端。

    参数 ``restricted_api_settings`` 包含无权限账号登录后取得的 JWT。
    返回用于验证 403 的 ``FactorComboAPI``；不得用于未登录 401 场景。
    """

    return FactorComboAPI(HTTPClient(restricted_api_settings))


@pytest.fixture(scope="session")
def factor_combo_non_owner_api(non_owner_api_settings: ApiSettings) -> FactorComboAPI:
    """创建具备业务权限但不拥有被测资源的组合因子客户端。

    参数 ``non_owner_api_settings`` 包含独立非所有者账号的 JWT。返回用于验证 404 所有权隔离的客户端；账号配置
    缺失时由 ``non_owner_account`` 明确跳过，不复用无权限账号。
    """

    return FactorComboAPI(HTTPClient(non_owner_api_settings))


@pytest.fixture(scope="session")
def factor_combo_unauthenticated_api(settings: Settings, live_mode: bool) -> FactorComboAPI:
    """创建不携带 Token 的组合因子接口客户端。

    参数 ``settings`` 提供基础地址和网络配置，``live_mode`` 表示是否显式启用真实环境。
    返回无鉴权 ``FactorComboAPI``；仅用于验证接口自身的 401 响应。
    """

    _validate_factor_combo_live_environment(settings, live_mode)
    api_settings = replace(settings.api, auth_token=None)
    return FactorComboAPI(HTTPClient(api_settings))


@pytest.fixture(scope="session")
def factor_combo_restricted_performance_api(restricted_api_settings: ApiSettings) -> PerformanceAPI:
    """创建无权限账号的 Performance Refresh 查询客户端。

    参数 ``restricted_api_settings`` 是无权限账号 JWT 配置。
    返回用于验证 ``manage_factor_library`` 权限边界的 Performance API 客户端。
    """

    return PerformanceAPI(HTTPClient(restricted_api_settings))


@pytest.fixture(scope="session")
def factor_combo_repository(settings: Settings, live_mode: bool) -> FactorComboRepository:
    """创建组合因子测试数据库仓储。

    参数 ``settings`` 提供数据库配置，``live_mode`` 表示是否显式启用真实环境。
    返回连接测试 MySQL 的 ``FactorComboRepository``；配置不完整时跳过，非测试环境时失败。
    """

    _validate_factor_combo_live_environment(settings, live_mode)
    if settings.database.driver != "mysql":
        pytest.skip("组合因子 live 测试需要 AUTOMATION_DB_DRIVER=mysql")
    required = (
        settings.database.host,
        settings.database.port,
        settings.database.name,
        settings.database.username,
        settings.database.password,
    )
    if not all(required):
        pytest.skip("组合因子 live 测试的 MySQL 环境变量配置不完整")
    return FactorComboRepository(DatabaseClient.from_settings(settings.database), settings.environment)


def _make_factor_combo_service(
    settings: Settings,
    account: AuthenticatedAccount,
    factor_combo_api: FactorComboAPI,
    repository: FactorComboRepository,
    scope: TestResourceScope,
) -> FactorComboService:
    """用统一依赖组装组合因子 Service，避免 Worker 与真实 E2E 使用不同协议客户端。

    参数 ``settings`` 提供 Agent 地址和流程配置，``account`` 提供当前 JWT、用户 ID 和网络配置，``factor_combo_api`` 与
    ``repository`` 分别是 Factor 协议客户端和数据库仓储，``scope`` 记录当前测试资源。返回配置完整的
    ``FactorComboService``；不执行网络请求或数据库操作。
    """

    agent_api = None
    if settings.factor_combo.agent_base_url:
        agent_api = AgentAPI(
            HTTPClient(replace(account.api_settings, base_url=settings.factor_combo.agent_base_url.rstrip("/")))
        )
    return FactorComboService(
        ChatAPI(HTTPClient(account.api_settings)),
        factor_combo_api,
        repository,
        settings.factor_combo,
        scope,
        agent_api=agent_api,
        performance_api=PerformanceAPI(HTTPClient(account.api_settings)),
        sub_factor_api=SubFactorAPI(HTTPClient(account.api_settings)),
        user_id=account.user_id,
    )


def _cleanup_factor_combo_resources(
    settings: Settings,
    scope: TestResourceScope,
    repository: FactorComboRepository,
) -> None:
    """在 pytest Fixture 生命周期结束时清理当前用例拥有的组合因子资源。

    参数 ``settings`` 提供测试数据清理开关，``scope`` 是 Fixture 创建的资源归属记录，``repository`` 负责测试库事务和
    实体删除。不返回值；未开启清理时保留数据，仓储发现异步任务未进入安全终态或数据库异常时继续抛出异常，不能静默放过。
    """

    if not settings.factor_combo.cleanup_test_data:
        return
    repository.clean_test_graph(scope.cleanable_resource_graph())


@pytest.fixture
def factor_combo_service(
    settings: Settings,
    privileged_account: AuthenticatedAccount,
    factor_combo_api: FactorComboAPI,
    factor_combo_repository: FactorComboRepository,
) -> Iterator[FactorComboService]:
    """为单个接口用例创建隔离的组合因子业务编排服务。

    参数来自测试环境配置、API 客户端和数据库仓储。
    生成函数级 ``FactorComboService``；用例结束后按配置清理当前用例创建的数据。
    """

    scope = TestResourceScope()
    service = _make_factor_combo_service(
        settings,
        privileged_account,
        factor_combo_api,
        factor_combo_repository,
        scope,
    )
    try:
        yield service
    finally:
        _cleanup_factor_combo_resources(settings, scope, factor_combo_repository)


@pytest.fixture
def factor_combo_worker_service(
    settings: Settings,
    factor_combo_service: FactorComboService,
) -> FactorComboService:
    """提供已确认允许执行 Worker 兼容接口的业务服务。

    参数 ``settings`` 提供 Worker 开关，``factor_combo_service`` 是函数级隔离服务。
    返回该服务；未显式开启 ``AUTOMATION_FACTOR_COMBO_WORKER_CONTRACTS`` 时跳过 Worker 合约用例。
    """

    if not settings.factor_combo.worker_contracts_enabled:
        pytest.skip("Worker 回调测试需要 AUTOMATION_FACTOR_COMBO_WORKER_CONTRACTS=true")
    return factor_combo_service


@pytest.fixture(scope="session")
def factor_combo_real_run_context(
    settings: Settings,
    privileged_account: AuthenticatedAccount,
    factor_combo_api: FactorComboAPI,
    factor_combo_repository: FactorComboRepository,
) -> Iterator[dict[str, Any]]:
    """启动一次真实组合 Agent Run 并保存首次与幂等重放响应。

    参数来自测试配置、组合 API 和数据库仓储。
    生成包含表单、工作单、两次启动响应及 ``RealRun`` 的字典；缺少 Agent UID 时跳过，接口失败时抛出异常。
    """

    if not settings.factor_combo.agent_base_url:
        pytest.skip("真实 Run 测试需要配置 AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL")
    scope = TestResourceScope()
    service = _make_factor_combo_service(
        settings,
        privileged_account,
        factor_combo_api,
        factor_combo_repository,
        scope,
    )
    try:
        form, _ = service.create_form_with_sub_factors()
        work_order_response = service.get_work_order_request(form.form_id)
        work_order_data = service.require_work_order(work_order_response, form)
        agent_selection = service.discover_agent(
            privileged_account.user_id,
            settings.factor_combo.agent_uid,
        )
        payload = {
            "agent_uid": agent_selection.agent_uid,
            "force_fresh_pipeline_run": False,
        }
        first_response = service.start_real_run_request(
            form,
            agent_uid=agent_selection.agent_uid,
            force_fresh_pipeline_run=False,
        )
        first_body = read_json_or_diagnostic(first_response)
        first_data = first_body.get("data") if isinstance(first_body, dict) else None
        run = service.parse_started_run_response(
            first_response,
            form,
            agent_uid=agent_selection.agent_uid,
        )
        if first_response.status_code != 202 or not isinstance(first_data, dict) or first_data.get("idempotent_replay") is not False:
            raise RuntimeError(
                "new real factor combo run must return HTTP 202 and idempotent_replay=false: "
                f"{first_body}"
            )
        replay_response = service.start_real_run_request(
            form,
            agent_uid=agent_selection.agent_uid,
            force_fresh_pipeline_run=False,
        )
        service.require_started_run_replay(
            replay_response,
            form,
            agent_uid=agent_selection.agent_uid,
            expected_pipeline_run_id=run.pipeline_run_id,
        )
        context = {
            "service": service,
            "form": form,
            "work_order_response": work_order_response,
            "work_order": work_order_data,
            "request_payload": payload,
            "agent_selection": agent_selection,
            "first_response": first_response,
            "replay_response": replay_response,
            "run": run,
        }
        yield context
    finally:
        _cleanup_factor_combo_resources(settings, scope, factor_combo_repository)


@pytest.fixture(scope="session")
def factor_combo_completed_real_run_context(
    factor_combo_real_run_context: dict[str, Any],
) -> dict[str, Any]:
    """轮询共享真实 Run 到终态并要求其成功完成。

    参数 ``factor_combo_real_run_context`` 是真实启动 Fixture 的上下文。
    返回增加状态快照和最终状态的字典；运行失败或未完成时抛出 ``AssertionError``，不会转成跳过或 xfail。
    """

    service = factor_combo_real_run_context["service"]
    run = factor_combo_real_run_context["run"]
    snapshots, final_status = service.poll_real_run(run)
    assert final_status.get("pipeline_status") == "completed", {
        "pipeline_run_id": run.pipeline_run_id,
        "status_snapshots": snapshots,
        "final_status": final_status,
    }
    return {
        **factor_combo_real_run_context,
        "status_snapshots": snapshots,
        "final_status": final_status,
    }


@pytest.fixture(scope="session")
def factor_combo_real_e2e_context(
    settings: Settings,
    privileged_account: AuthenticatedAccount,
    factor_combo_api: FactorComboAPI,
    factor_combo_repository: FactorComboRepository,
) -> Iterator[dict[str, Any]]:
    """执行一条完整真实研究链路，并把最终分类和所有诊断数据交给 E2E 用例。

    参数来自测试配置、已校验账号、Factor API 和测试数据库。
    生成包含表单、Agent、完整研究结果和清理服务的上下文；缺少 Agent 地址时跳过，真实业务失败不会被转换为跳过或
    xfail，而是由调用用例根据 ``FactorComboFlowError`` 记录分类。
    """

    if not settings.factor_combo.agent_base_url:
        pytest.skip("完整真实 E2E 需要配置 AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL")
    scope = TestResourceScope()
    service = _make_factor_combo_service(
        settings,
        privileged_account,
        factor_combo_api,
        factor_combo_repository,
        scope,
    )
    try:
        form, choices = service.create_form_with_sub_factors()
        work_order_response = service.get_work_order_request(form.form_id)
        work_order_data = service.require_work_order(work_order_response, form)
        flow = service.run_real_research_flow(
            form,
            privileged_account.user_id,
            preferred_agent_uid=settings.factor_combo.agent_uid,
        )
        yield {
            "service": service,
            "form": form,
            "choices": choices,
            "work_order": work_order_data,
            "flow": flow,
        }
    finally:
        _cleanup_factor_combo_resources(settings, scope, factor_combo_repository)


def _authenticate_account(
    api_settings: ApiSettings,
    credentials: AccountCredentials | None,
    account_label: str,
    *,
    required_permissions: set[str],
) -> AuthenticatedAccount:
    """登录并验证一套测试账号或静态 Token 的完整身份上下文。

    参数 ``api_settings`` 提供地址和网络策略，``credentials`` 提供可选账号密码，``account_label`` 仅用于脱敏错误定位，
    ``required_permissions`` 是本场景必须具备的权限集合。返回 ``AuthenticatedAccount``；登录失败、/me 响应结构错误、
    账号未 approved、身份不匹配或权限缺失时抛出 ``RuntimeError``，异常中不包含密码和完整 Token。
    """

    authenticated_settings: ApiSettings
    if credentials is not None:
        if not credentials.email or not credentials.password:
            raise ValueError("Account credentials must contain both email and password")
        unauthenticated_settings = replace(api_settings, auth_token=None)
        login_response = AuthAPI(HTTPClient(unauthenticated_settings)).login(
            credentials.email,
            credentials.password,
        )
        login_body = _response_json_object(login_response, f"{account_label}账号登录")
        if login_response.status_code != 200 or login_body.get("success") is not True:
            error_message = login_body.get("error") or login_body.get("message") or "unknown login error"
            raise RuntimeError(
                f"{account_label}账号登录失败: HTTP {login_response.status_code}, error={error_message!r}"
            )
        try:
            token = AuthResponsePayload.token(login_body)
        except ValueError as error:
            raise RuntimeError(f"{account_label}账号登录响应缺少有效 Token") from error
        authenticated_settings = replace(api_settings, auth_token=token)
    elif not api_settings.auth_token:
        raise ValueError("Either account credentials or an auth token must be configured")
    else:
        authenticated_settings = api_settings

    current_user_response = AuthAPI(HTTPClient(authenticated_settings)).get_current_user()
    current_user_body = _response_json_object(current_user_response, f"{account_label}账号身份校验")
    if current_user_response.status_code != 200 or current_user_body.get("success") is not True:
        raise RuntimeError(f"{account_label}账号 JWT 无法通过 /me 校验: HTTP {current_user_response.status_code}")
    try:
        current_user = AuthResponsePayload.user(current_user_body)
    except ValueError as error:
        raise RuntimeError(f"{account_label}账号 /me 响应缺少用户数据") from error
    try:
        user_id = AuthResponsePayload.user_id(current_user)
    except ValueError as error:
        raise RuntimeError(f"{account_label}账号 /me 响应缺少有效 user_id") from error
    actual_email = current_user.get("email")
    if not isinstance(actual_email, str) or not actual_email.strip():
        raise RuntimeError(f"{account_label}账号 /me 响应缺少有效 email")
    if credentials is not None and actual_email.casefold() != credentials.email.casefold():
        raise RuntimeError(f"{account_label}账号登录身份与配置邮箱不一致")
    status = str(current_user.get("status", "")).strip().lower()
    if status != "approved":
        raise RuntimeError(f"{account_label}账号状态不是 approved: {status or 'missing'}")
    try:
        permissions = AuthResponsePayload.permissions(current_user)
    except ValueError:
        permissions = frozenset()
    missing_permissions = sorted(required_permissions - permissions)
    if missing_permissions:
        raise RuntimeError(f"{account_label}账号缺少必要权限: {', '.join(missing_permissions)}")
    return AuthenticatedAccount(
        api_settings=authenticated_settings,
        user_id=user_id,
        email=actual_email.strip(),
        status=status,
        permissions=permissions,
    )


def _response_json_object(response: Any, action: str) -> dict[str, Any]:
    """读取认证响应 JSON 且不泄露敏感正文。

    参数 ``response`` 是 requests 兼容响应，``action`` 是脱敏后的操作名称。
    返回 JSON 对象；响应不是合法 JSON 对象时抛出 ``RuntimeError``，异常中不包含 Token 或密码。
    """

    try:
        body = read_json_object(response, action)
    except ValueError as error:
        raise RuntimeError(f"{action}返回了非 JSON 响应: HTTP {response.status_code}") from error
    if not isinstance(body, dict):
        raise RuntimeError(f"{action}返回的 JSON 根节点不是对象: HTTP {response.status_code}")
    return body


def _validate_factor_combo_live_environment(settings: Settings, live_mode: bool) -> None:
    """校验组合因子真实测试的环境边界和基础地址。

    参数 ``settings`` 是完整测试配置，``live_mode`` 表示是否允许外部访问。
    不返回值；未启用或缺少地址时跳过，非 test、生产地址或错误 API 路径时使测试失败。
    """

    if not live_mode:
        pytest.skip("真实组合因子测试默认关闭，请显式传入 --live")
    if settings.environment.strip().lower() != "test":
        pytest.fail("组合因子数据库写入测试只允许 --env test")
    if not settings.api.base_url:
        pytest.skip("需要通过环境变量配置 AUTOMATION_API_BASE_URL")
    parsed = urlparse(settings.api.base_url)
    if parsed.hostname == "factor-backend.questvector.ai":
        pytest.fail("禁止对生产环境执行组合因子自动化测试")
    if not parsed.path.rstrip("/").endswith("/api/v1"):
        pytest.fail("AUTOMATION_API_BASE_URL 必须包含 /api/v1")
    if settings.factor_combo.agent_base_url:
        agent_parsed = urlparse(settings.factor_combo.agent_base_url)
        if agent_parsed.hostname in {
            "factor-frontend.questvector.ai",
            "factor-backend.questvector.ai",
        }:
            pytest.fail("禁止使用生产环境 Agent 地址执行组合因子自动化测试")
        if not agent_parsed.path.rstrip("/").endswith("/api/v2"):
            pytest.fail("AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL 必须包含 /api/v2")
