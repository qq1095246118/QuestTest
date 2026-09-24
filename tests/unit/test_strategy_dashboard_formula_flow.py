"""测试写入流程的故障路径；替身结果不计入产品公式通过数。"""

from copy import deepcopy
from decimal import Decimal
import json

import pytest
import requests

from api.client import HTTPClient
from api.strategy_dashboard_formula_api import FormulaFlowEndpointConfig, FormulaInjectionContractError, StrategyDashboardFormulaAPI
from config.settings import ApiSettings
from service.strategy_dashboard_formula_flow import FormulaFlow, FormulaFlowBlocked, FormulaFlowError
from service.strategy_dashboard_formula_scenarios import FormulaScenario
from tests.strategy_dashboard_formula_assertions import assert_formula_values


pytestmark = pytest.mark.unit
ENDPOINTS = FormulaFlowEndpointConfig("/fixtures", "/calculations", "/calculations/{run_id}", "/calculations/{run_id}/result", "/fixtures/{fixture_id}")
SCENARIO = FormulaScenario("FM-wallet-001", "权益减浮盈得到钱包", "wallet", ("CALC-001",), {"E": "120", "U": "20"}, {"wallet": "100"})


def response(data=None, code=200):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps({"data": data}).encode()
    return result


class FixtureServer:
    """只用于测试客户端编排的固定响应服务；不会计算被测公式。"""

    def __init__(self):
        self.requests = []
        self.states = ["queued", "completed"]
        self.result = {"wallet": "100"}
        self.cleanup_code = 204
        self.stage_fault = None
        self.wrong_identity = None
        self.owner = None

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, deepcopy(kwargs)))
        if method == "POST" and url.endswith("/fixtures"):
            self.owner = kwargs["json"]["request_id"]
            data = {"request_id": self.owner, "fixture_id": "fixture-1"}
            stage = "write"
        elif method == "POST":
            data = {"request_id": self.owner, "fixture_id": "fixture-1", "run_id": "run-1"}
            stage = "trigger"
        elif method == "DELETE":
            return response({"fixture_id": "fixture-1", "deleted": True}, self.cleanup_code)
        elif url.endswith("/result"):
            data = {"request_id": self.owner, "fixture_id": "fixture-1", "run_id": "run-1", "result": self.result}
            stage = "result"
        else:
            status = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            data = {"fixture_id": "fixture-1", "run_id": "run-1", "status": status}
            stage = "status"
        if self.stage_fault == stage:
            raise requests.Timeout("synthetic uncertain request")
        if self.wrong_identity == stage:
            data["fixture_id" if stage != "write" else "request_id"] = "somebody-else"
        return response(data)


def build_flow(server=None, **options):
    server = server or FixtureServer()
    settings = ApiSettings("https://test.example", 1, 0, 0, None)
    api = StrategyDashboardFormulaAPI(HTTPClient(settings, server), ENDPOINTS, environment="test", base_url=settings.base_url, allowed_hosts=("test.example",))
    return server, FormulaFlow(api, pause=lambda seconds: None, **options)


def test_raw_payload_never_sends_expected_and_result_is_independent():
    server, flow = build_flow()
    with flow.execute(SCENARIO) as run:
        assert_formula_values(run.result, SCENARIO.expected)
    assert run.cleanup_status == "deleted"
    source = server.requests[0][2]["json"]
    assert source["source"] == {"E": "120", "U": "20"}
    assert "expected" not in source and "result" not in source
    assert server.requests[-1][1].endswith("/fixtures/fixture-1")
    assert [event["stage"] for event in run.events][-1] == "cleanup"


def test_repeat_scenario_gets_new_identity():
    _, flow = build_flow()
    with flow.execute(SCENARIO) as first:
        pass
    with flow.execute(SCENARIO) as second:
        pass
    assert first.request_id != second.request_id


def test_wrong_formula_fails_business_assertion_and_still_cleans():
    server, flow = build_flow()
    server.result = {"wallet": "140"}
    with pytest.raises(AssertionError, match="wallet"):
        with flow.execute(SCENARIO) as run:
            assert_formula_values(run.result, SCENARIO.expected)
    assert run.cleanup_status == "deleted"


@pytest.mark.parametrize("stage", ["write", "trigger", "status", "result"])
def test_foreign_request_or_run_cannot_pass(stage):
    server, flow = build_flow()
    server.wrong_identity = stage
    with pytest.raises(FormulaFlowError, match="不匹配"):
        with flow.execute(SCENARIO):
            pytest.fail("must not yield foreign data")
    deletes = [call for call in server.requests if call[0] == "DELETE"]
    assert len(deletes) == (1 if stage == "result" else 0)


@pytest.mark.parametrize("stage", ["write", "trigger", "status"])
def test_uncertain_write_or_active_task_retains_resources(stage):
    server, flow = build_flow()
    server.stage_fault = stage
    with pytest.raises(requests.Timeout):
        with flow.execute(SCENARIO):
            pass
    assert not any(call[0] == "DELETE" for call in server.requests)
    assert sum(call[0] == "POST" for call in server.requests) <= 2


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_known_terminal_failure_cleans_exact_fixture(state):
    server, flow = build_flow()
    server.states = [state]
    with pytest.raises(FormulaFlowError, match="终态"):
        with flow.execute(SCENARIO):
            pass
    assert server.requests[-1][1].endswith("/fixtures/fixture-1")


def test_unknown_status_is_not_silently_success_or_cleaned():
    server, flow = build_flow()
    server.states = ["mystery"]
    with pytest.raises(FormulaFlowError, match="状态未知"):
        with flow.execute(SCENARIO):
            pass
    assert server.requests[-1][0] == "GET"


def test_poll_timeout_keeps_source_for_running_task():
    values = iter([0, 2])
    server, flow = build_flow(timeout_seconds=1, clock=lambda: next(values))
    server.states = ["running"]
    with pytest.raises(FormulaFlowError, match="轮询超时"):
        with flow.execute(SCENARIO):
            pass
    assert not any(call[0] == "DELETE" for call in server.requests)


def test_cleanup_failure_prevents_passing():
    server, flow = build_flow()
    server.cleanup_code = 500
    with pytest.raises(FormulaFlowError, match="清理"):
        with flow.execute(SCENARIO):
            pass


def test_cleanup_failure_keeps_original_formula_failure():
    server, flow = build_flow()
    server.cleanup_code = 500
    server.result = {"wallet": "999"}
    with pytest.raises(AssertionError) as captured:
        with flow.execute(SCENARIO) as run:
            assert_formula_values(run.result, SCENARIO.expected)
    assert run.cleanup_status == "failed"
    assert "清理失败" in " ".join(captured.value.__notes__)


def test_unconfigured_injection_is_explicitly_blocked_without_network():
    server = FixtureServer()
    api = StrategyDashboardFormulaAPI(HTTPClient(ApiSettings("https://test.example", 1, 0, 0, None), server), FormulaFlowEndpointConfig("", "", "", "", ""), environment="test", base_url="https://test.example", allowed_hosts=("test.example",))
    with pytest.raises(FormulaFlowBlocked, match="BLOCKED_INJECTION_ENDPOINT"):
        with FormulaFlow(api).execute(SCENARIO):
            pass
    assert not server.requests


@pytest.mark.parametrize("environment,base", [("production", "https://test.example"), ("test", "https://production.example"), ("test", "https://user@ test.example")])
def test_environment_mismatch_is_blocked_before_request(environment, base):
    with pytest.raises(FormulaInjectionContractError):
        StrategyDashboardFormulaAPI(None, ENDPOINTS, environment=environment, base_url=base, allowed_hosts=("test.example",))


@pytest.mark.parametrize("path", ["https://elsewhere/fixtures", "//elsewhere/fixtures", "/fixtures/../accounts", "/fixtures?drop_all=true"])
def test_bad_paths_are_rejected(path):
    with pytest.raises(FormulaInjectionContractError):
        FormulaFlowEndpointConfig(path, ENDPOINTS.trigger_path, ENDPOINTS.status_path_template, ENDPOINTS.result_path_template, ENDPOINTS.cleanup_path_template).validate()


@pytest.mark.parametrize("actual,expected", [({}, {"value": None}), ({"value": "None"}, {"value": None}), ({"value": "NaN"}, {"value": "0"}), ({"value": "Infinity"}, {"value": "1"}), ({"value": True}, {"value": "1"}), ({"flag": 1}, {"flag": True}), ({"value": "1.01"}, {"value": "1"})])
def test_formula_comparison_rejects_false_passes(actual, expected):
    with pytest.raises(AssertionError):
        assert_formula_values(actual, expected)


def test_equivalent_decimal_strings_and_explicit_null_are_accepted():
    assert_formula_values({"value": "100.0000", "items": [None, False, "0.333333333333"]}, {"value": "100", "items": [None, False, "0.3333333333333333333"]})


def test_zero_tolerance_enforces_bill_precision():
    with pytest.raises(AssertionError):
        assert_formula_values("1.00000001", "1.00000000", tolerance=Decimal(0))


def test_declared_test_url_cannot_hide_actual_production_client():
    client = HTTPClient(ApiSettings("https://production.example", 1, 0, 0, None), FixtureServer())
    with pytest.raises(FormulaInjectionContractError, match="实际地址"):
        StrategyDashboardFormulaAPI(client, ENDPOINTS, environment="test", base_url="https://test.example", allowed_hosts=("test.example",))


def test_fixture_requests_never_follow_redirects():
    server, flow = build_flow()
    with flow.execute(SCENARIO):
        pass
    assert all(call[2]["allow_redirects"] is False for call in server.requests)


def test_http200_business_error_is_not_success():
    from service.strategy_dashboard_formula_flow import _body
    raw = response({"fixture_id": "fixture-1"})
    raw._content = b'{"code":500,"data":{"fixture_id":"fixture-1"}}'
    with pytest.raises(FormulaFlowError, match="业务错误码"):
        _body(raw, "write_fixture")


def test_numeric_looking_business_id_is_not_decimal_normalized():
    with pytest.raises(AssertionError, match="identity differs"):
        assert_formula_values({"id": "001"}, {"id": "1"})


def test_business_count_cannot_use_amount_tolerance():
    with pytest.raises(AssertionError):
        assert_formula_values({"order_count": "2.0000000000001"}, {"order_count": 2})


@pytest.mark.parametrize("success", [False, 0, "false"])
def test_nontrue_success_flag_cannot_authorize_followup(success):
    from service.strategy_dashboard_formula_flow import _body
    raw = response({"fixture_id": "fixture-1"})
    raw._content = json.dumps({"success": success, "data": {"fixture_id": "fixture-1"}}).encode()
    with pytest.raises(FormulaFlowError, match="非成功业务响应"):
        _body(raw, "write_fixture")
