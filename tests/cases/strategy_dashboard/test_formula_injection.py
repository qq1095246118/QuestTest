"""真正访问后端的原始数据公式验收。默认不执行写入，不回退到 contract。"""

from dataclasses import asdict, replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from api.auth_api import AuthAPI, AuthResponsePayload
from api.client import HTTPClient
from api.strategy_dashboard_formula_api import FormulaFlowEndpointConfig, FormulaInjectionContractError, StrategyDashboardFormulaAPI
from service.strategy_dashboard_formula_flow import FormulaFlow, FormulaFlowBlocked
from service.strategy_dashboard_formula_scenarios import formula_catalog_paths, load_formula_catalog
from tests.strategy_dashboard_formula_routes import source_case_routes
from tests.strategy_dashboard_formula_assertions import assert_formula_values


_ROOT = Path(__file__).resolve().parents[3]
_CATALOG = load_formula_catalog(formula_catalog_paths(_ROOT))
# 已绑定真实数据库输入和公开查询接口的场景由对应 source 文件执行一次。
# 其余场景仍保留为明确的未绑定/规则待定，不因缺适配器从统计中消失。
_SOURCE_CASE_IDS = source_case_routes()
_CASES = tuple(case for case in _CATALOG if case.scenario_id not in _SOURCE_CASE_IDS)
pytestmark = [pytest.mark.integration, pytest.mark.strategy_dashboard_formula]


@pytest.fixture(scope="module")
def formula_flow(settings, pytestconfig):
    """仅在显式 live/test 下登录并准备客户端；未绑定入口时报阻断。"""
    if not pytestconfig.getoption("--live"):
        pytest.skip("NOT_RUN_LIVE: 原始数据公式用例需要 --live --env test")
    if pytestconfig.getoption("--env") != "test" or settings.environment != "test":
        pytest.fail("BLOCKED_ENV: 原始数据写入必须显式选择 --env test", pytrace=False)
    endpoints = FormulaFlowEndpointConfig.from_environment()
    try:
        endpoints.validate()
    except FormulaInjectionContractError as exc:
        pytest.fail(f"BLOCKED_INJECTION_ENDPOINT: {exc}", pytrace=False)
    client = HTTPClient(settings.api)
    # 在登录前检查目标；不使用生产地址或已有资金账号准备样本。
    StrategyDashboardFormulaAPI(client, endpoints, environment=settings.environment,
                                base_url=settings.api.base_url, allowed_hosts=settings.environment_safety.allowed_hosts)
    try:
        if settings.api.auth_token:
            authenticated = settings.api
        else:
            credentials = settings.authentication.privileged
            if not credentials.email or not credentials.password:
                pytest.fail("BLOCKED_AUTH: 缺少测试登录配置", pytrace=False)
            response = AuthAPI(client).login(credentials.email, credentials.password)
            if response.status_code != 200:
                pytest.fail(f"BLOCKED_AUTH: 登录 HTTP {response.status_code}", pytrace=False)
            authenticated = replace(settings.api, auth_token=AuthResponsePayload.token(response.json()))
    finally:
        client.close()
    api = StrategyDashboardFormulaAPI(HTTPClient(authenticated), endpoints, environment=settings.environment,
                                      base_url=settings.api.base_url, allowed_hosts=settings.environment_safety.allowed_hosts)
    output = _ROOT / "reports" / f"quant-formula-injection-{uuid4().hex}"
    output.mkdir(parents=True)

    def save_evidence(result):
        # 只有测试源数据、身份、计算输出；不保存 Authorization、登录响应或配置。
        (output / f"{result.request_id}.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        yield FormulaFlow(api, evidence_sink=save_evidence)
    finally:
        api.close()


@pytest.mark.parametrize("scenario", _CASES, ids=lambda case: case.scenario_id)
def test_raw_input_reaches_formula_result(scenario, request, record_property):
    """提交原始输入，等待真计算，再对比固定预期并清理本次资源。"""
    record_property("formula_family", scenario.family)
    record_property("case_ids", ",".join(scenario.case_ids))
    if scenario.expected.get("status") == "blocked_contract":
        pytest.skip(f"BLOCKED_CONTRACT: {scenario.title}")
    flow = request.getfixturevalue("formula_flow")
    try:
        with flow.execute(scenario) as execution:
            record_property("request_id", execution.request_id)
            record_property("fixture_id", execution.fixture_id)
            record_property("run_id", execution.run_id)
            assert_formula_values(execution.result, scenario.expected, tolerance=scenario.tolerance)
        assert execution.cleanup_status == "deleted", "资源清理未完成，不能通过"
    except FormulaFlowBlocked as exc:
        pytest.fail(str(exc), pytrace=False)
