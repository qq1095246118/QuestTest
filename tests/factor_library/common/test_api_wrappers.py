import pytest

from api.platform.admin_api import AdminAPI
from api.platform.approval_api import ApprovalAPI
from api.platform.auth_api import AuthAPI
from api.platform.factor_api import FactorAPI
from api.platform.factor_ic_api import FactorICAPI
from service.common.http.http_client import HTTPClient


class TestAPIWrappers:
    """原始 API wrapper 路由测试。

    请求参数:
        使用 monkeypatch 替换底层 request 方法，捕获 method、url、params 和 json。
    返回值:
        无返回值；pytest 根据捕获结果验证 wrapper 是否覆盖接口文档中的路由。
    """

    @pytest.mark.parametrize(
        "api_factory,call_name,args,kwargs,expected_method,expected_path,expected_params,expected_json",
        [
            (lambda: AuthAPI(), "register", (), {"email": "auto@example.com", "password": "Pwd123456", "display_name": "Auto"}, "POST", "/api/v1/auth/register", None, {"email": "auto@example.com", "password": "Pwd123456", "display_name": "Auto"}),
            (lambda: AuthAPI(), "login", (), {"email": "auto@example.com", "password": "Pwd123456"}, "POST", "/api/v1/auth/login", None, {"email": "auto@example.com", "password": "Pwd123456"}),
            (lambda: AuthAPI(), "me", (), {"token": "token"}, "GET", "/api/v1/me", {}, None),
            (lambda: ApprovalAPI(token="token"), "list_approvals", (), {"status": "pending", "target_type": "factor", "page": 1, "limit": 20}, "GET", "/api/v1/approvals", {"status": "pending", "target_type": "factor", "page": 1, "limit": 20}, None),
            (lambda: ApprovalAPI(token="token"), "create_approval", ({"request_type": "update", "target_type": "factor", "target_id": 123},), {}, "POST", "/api/v1/approvals", None, {"request_type": "update", "target_type": "factor", "target_id": 123}),
            (lambda: ApprovalAPI(token="token"), "get_approval", (8,), {}, "GET", "/api/v1/approvals/8", {}, None),
            (lambda: ApprovalAPI(token="token"), "process_approval", (8, "approve"), {"comment": "ok"}, "PATCH", "/api/v1/approvals/8", None, {"action": "approve", "comment": "ok"}),
            (lambda: ApprovalAPI(token="token"), "cancel_approval", (8,), {}, "DELETE", "/api/v1/approvals/8", None, None),
            (lambda: ApprovalAPI(token="token"), "batch_approve", ([8, 9],), {"comment": "batch"}, "POST", "/api/v1/approvals/batch/approve", None, {"approval_ids": [8, 9], "comment": "batch"}),
            (lambda: FactorAPI(token="token"), "list_factors", (), {"page": 1, "limit": 5, "status": 1}, "GET", "/api/v1/factors", {"page": 1, "limit": 5, "status": 1}, None),
            (lambda: FactorAPI(token="token"), "create_factor", ({"serial_prefix": "AUTO", "factor_name": "auto_factor"},), {}, "POST", "/api/v1/factors", None, {"serial_prefix": "AUTO", "factor_name": "auto_factor"}),
            (lambda: FactorAPI(token="token"), "get_factor", (123,), {}, "GET", "/api/v1/factors/123", {}, None),
            (lambda: FactorAPI(token="token"), "update_factor", (123, {"cn_name": "自动因子"}), {}, "PUT", "/api/v1/factors/123", None, {"cn_name": "自动因子"}),
            (lambda: FactorAPI(token="token"), "update_factor_status", (123, 2), {}, "PUT", "/api/v1/factors/123/status", None, {"status": 2}),
            (lambda: FactorAPI(token="token"), "batch_update_factor_status", ([123, 124], 3), {}, "PUT", "/api/v1/factors/status/batch", None, {"factor_ids": [123, 124], "status": 3}),
            (lambda: FactorAPI(token="token"), "update_factor_with_approval", (123, {"cn_name": "new"}), {}, "PUT", "/api/v1/factors/123/with-approval", None, {"cn_name": "new"}),
            (lambda: FactorAPI(token="token"), "update_factor_status_with_approval", (123, 2), {}, "PUT", "/api/v1/factors/123/status/with-approval", None, {"status": 2}),
            (lambda: FactorAPI(token="token"), "batch_update_factor_status_with_approval", ([123, 124], 3), {}, "PUT", "/api/v1/factors/status/batch/with-approval", None, {"factor_ids": [123, 124], "status": 3}),
            (lambda: FactorAPI(token="token"), "notify_factor_result", ("run-1",), {}, "POST", "/api/v1/factors/notification", None, {"run_id": "run-1"}),
            (lambda: FactorAPI(token="token"), "list_factor_evaluation_standards", (), {"time_window": "1d", "coin_category": "main"}, "GET", "/api/v1/factor-evaluation-standards", {"time_window": "1d", "coin_category": "main"}, None),
            (lambda: FactorAPI(token="token"), "copy_factors", ([123],), {}, "POST", "/api/v1/factors/copy", None, {"factor_ids": [123]}),
            (lambda: FactorAPI(token="token"), "list_coin_universe_symbols", (), {"universe_key": "main", "is_active": 1}, "GET", "/api/v1/coin-universe-symbols", {"universe_key": "main", "is_active": 1}, None),
            (lambda: FactorAPI(token="token"), "list_factor_filter_options", (), {"status": 1}, "GET", "/api/v1/factors/filter-options", {"status": 1}, None),
            (lambda: FactorAPI(token="token"), "get_factors_graph", (), {"type": "new", "from_date": "2026-01-01", "to_date": "2026-06-10"}, "GET", "/api/v1/factors/graph", {"type": "new", "from": "2026-01-01", "to": "2026-06-10"}, None),
            (lambda: FactorAPI(token="token"), "list_factor_theme_tree", (), {"factor_theme": "momentum"}, "GET", "/api/v1/factors/theme-tree", {"factor_theme": "momentum"}, None),
            (lambda: FactorAPI(token="token"), "list_themes", (), {"theme_key": "momentum"}, "GET", "/api/v1/themes", {"theme_key": "momentum"}, None),
            (lambda: FactorAPI(token="token"), "create_theme", ({"theme_key": "auto_theme"},), {}, "POST", "/api/v1/themes", None, {"theme_key": "auto_theme"}),
            (lambda: FactorAPI(token="token"), "get_theme", (88,), {}, "GET", "/api/v1/themes/88", {}, None),
            (lambda: FactorAPI(token="token"), "update_theme", (88, {"theme_name": "auto"}), {}, "PUT", "/api/v1/themes/88", None, {"theme_name": "auto"}),
            (lambda: FactorAPI(token="token"), "update_theme_status", (88, 3), {}, "PUT", "/api/v1/themes/88/status", None, {"status": 3}),
            (lambda: FactorAPI(token="token"), "update_theme_with_approval", (88, {"theme_name": "new"}), {}, "PUT", "/api/v1/themes/88/with-approval", None, {"theme_name": "new"}),
            (lambda: FactorAPI(token="token"), "update_theme_status_with_approval", (88, 3), {}, "PUT", "/api/v1/themes/88/status/with-approval", None, {"status": 3}),
            (lambda: FactorAPI(token="token"), "list_sub_factors", (), {"page": 1, "limit": 5, "status": 1}, "GET", "/api/v1/sub-factors", {"page": 1, "limit": 5, "status": 1}, None),
            (lambda: FactorAPI(token="token"), "create_sub_factor", ({"serial_prefix": "AUTO", "sub_factor_name": "auto_sub"},), {}, "POST", "/api/v1/sub-factors", None, {"serial_prefix": "AUTO", "sub_factor_name": "auto_sub"}),
            (lambda: FactorAPI(token="token"), "list_sub_factor_summary", (), {"type": "new", "page": 1, "limit": 5}, "GET", "/api/v1/sub-factors/summary", {"type": "new", "page": 1, "limit": 5}, None),
            (lambda: FactorAPI(token="token"), "get_sub_factors_graph", (), {"type": "valid", "from_date": "2026-01-01", "to_date": "2026-06-10"}, "GET", "/api/v1/sub-factors/graph", {"type": "valid", "from": "2026-01-01", "to": "2026-06-10"}, None),
            (lambda: FactorAPI(token="token"), "get_sub_factor_earliest_date", (), {}, "GET", "/api/v1/sub-factors/earliest-date", {}, None),
            (lambda: FactorAPI(token="token"), "get_sub_factor", (66,), {}, "GET", "/api/v1/sub-factors/66", {}, None),
            (lambda: FactorAPI(token="token"), "update_sub_factor", (66, {"cn_name": "自动子因子"}), {}, "PUT", "/api/v1/sub-factors/66", None, {"cn_name": "自动子因子"}),
            (lambda: FactorAPI(token="token"), "update_sub_factor_status", (66, 2), {}, "PUT", "/api/v1/sub-factors/66/status", None, {"status": 2}),
            (lambda: FactorAPI(token="token"), "batch_update_sub_factor_status", ([66], 3), {}, "PUT", "/api/v1/sub-factors/status/batch", None, {"sub_factor_ids": [66], "status": 3}),
            (lambda: FactorAPI(token="token"), "update_sub_factor_with_approval", (66, {"cn_name": "new"}), {}, "PUT", "/api/v1/sub-factors/66/with-approval", None, {"cn_name": "new"}),
            (lambda: FactorAPI(token="token"), "update_sub_factor_status_with_approval", (66, 2), {}, "PUT", "/api/v1/sub-factors/66/status/with-approval", None, {"status": 2}),
            (lambda: FactorAPI(token="token"), "batch_update_sub_factor_status_with_approval", ([66, 67], 3), {}, "PUT", "/api/v1/sub-factors/status/batch/with-approval", None, {"sub_factor_ids": [66, 67], "status": 3}),
            (lambda: FactorAPI(token="token"), "copy_sub_factors", ([66],), {}, "POST", "/api/v1/sub-factors/copy", None, {"sub_factor_ids": [66]}),
            (lambda: FactorAPI(token="token"), "list_sub_factor_filter_options", (), {"status": 1}, "GET", "/api/v1/sub-factors/filter-options", {"status": 1}, None),
            (lambda: FactorICAPI(token="token"), "get_factor_slice_metrics", (123,), {"ic_scope": "time_series", "symbol": "BTCUSDT"}, "GET", "/api/v1/factor-ic/factors/123/slice-metrics", {"ic_scope": "time_series", "symbol": "BTCUSDT"}, None),
            (lambda: FactorICAPI(token="token"), "get_factor_summary", (123,), {"ic_scope": "time_series", "time_window": "1d"}, "GET", "/api/v1/factor-ic/factors/123/summary", {"ic_scope": "time_series", "time_window": "1d"}, None),
            (lambda: FactorICAPI(token="token"), "get_factor_symbol_window_metrics", (123,), {"universe_key": "main", "limit": 5}, "GET", "/api/v1/factor-ic/factors/123/symbol-window-metrics", {"universe_key": "main", "limit": 5}, None),
            (lambda: FactorICAPI(token="token"), "get_sub_factor_slice_metrics", (66,), {"ic_scope": "cross_sectional", "symbol": "BTCUSDT"}, "GET", "/api/v1/factor-ic/sub-factors/66/slice-metrics", {"ic_scope": "cross_sectional", "symbol": "BTCUSDT"}, None),
            (lambda: FactorICAPI(token="token"), "get_sub_factor_summary", (66,), {"ic_scope": "cross_sectional", "time_window": "1d"}, "GET", "/api/v1/factor-ic/sub-factors/66/summary", {"ic_scope": "cross_sectional", "time_window": "1d"}, None),
            (lambda: FactorICAPI(token="token"), "get_sub_factor_symbol_window_metrics", (66,), {"universe_key": "altcoin", "limit": 5}, "GET", "/api/v1/factor-ic/sub-factors/66/symbol-window-metrics", {"universe_key": "altcoin", "limit": 5}, None),
            (lambda: FactorICAPI(token="token"), "batch_upsert_summary_metrics", ([{"factor_id": 123}],), {}, "POST", "/api/v1/factor-ic/summary-metrics/batch", None, {"items": [{"factor_id": 123}]}),
            (lambda: FactorICAPI(token="token"), "list_slice_metrics", (), {"factor_id": 123, "symbol": "BTCUSDT", "limit": 5}, "GET", "/api/v1/factor-ic/slice-metrics", {"factor_id": 123, "symbol": "BTCUSDT", "limit": 5}, None),
            (lambda: FactorICAPI(token="token"), "list_slice_metrics", (), {"is_sub_factor_id": False, "symbol": "BTCUSDT", "limit": 5}, "GET", "/api/v1/factor-ic/slice-metrics", {"is_sub_factor_id": 0, "symbol": "BTCUSDT", "limit": 5}, None),
            (lambda: FactorICAPI(token="token"), "batch_upsert_slice_metrics", ([{"factor_id": 123}],), {}, "POST", "/api/v1/factor-ic/slice-metrics/batch", None, {"items": [{"factor_id": 123}]}),
            (lambda: FactorICAPI(token="token"), "list_runs", (), {"factor_id": 123, "limit": 5}, "GET", "/api/v1/factor-ic/runs", {"factor_id": 123, "limit": 5}, None),
            (lambda: FactorICAPI(token="token"), "create_run", ({"factor_id": 123, "ic_scope": "time_series"},), {}, "POST", "/api/v1/factor-ic/runs", None, {"factor_id": 123, "ic_scope": "time_series"}),
            (lambda: FactorICAPI(token="token"), "get_run", (456,), {}, "GET", "/api/v1/factor-ic/runs/456", {}, None),
            (lambda: FactorICAPI(token="token"), "list_scoring_standards", (), {"time_window": "1d", "coin_category": "main"}, "GET", "/api/v1/factor-ic/scoring-standards", {"time_window": "1d", "coin_category": "main"}, None),
            (lambda: AdminAPI(token="token"), "list_users", (), {"status": 1}, "GET", "/api/v1/admin/users", {"status": 1}, None),
            (lambda: AdminAPI(token="token"), "update_user", (7, {"status": 1}), {}, "PATCH", "/api/v1/admin/users/7", None, {"status": 1}),
            (lambda: AdminAPI(token="token"), "delete_user", (7,), {}, "DELETE", "/api/v1/admin/users/7", None, None),
            (lambda: AdminAPI(token="token"), "get_user_permissions", (7,), {}, "GET", "/api/v1/admin/users/7/permissions", {}, None),
            (lambda: AdminAPI(token="token"), "replace_user_permissions", (7, ["factor.read"]), {}, "PUT", "/api/v1/admin/users/7/permissions", None, {"perm_codes": ["factor.read"]}),
            (lambda: AdminAPI(token="token"), "grant_user_permission", (7, "factor.read"), {}, "POST", "/api/v1/admin/users/7/permissions/factor.read", None, None),
            (lambda: AdminAPI(token="token"), "revoke_user_permission", (7, "factor.read"), {}, "DELETE", "/api/v1/admin/users/7/permissions/factor.read", None, None),
            (lambda: AdminAPI(token="token"), "list_permissions", (), {}, "GET", "/api/v1/admin/permissions", {}, None),
            (lambda: AdminAPI(token="token"), "list_role_templates", (), {}, "GET", "/api/v1/admin/role-templates", {}, None),
            (lambda: AdminAPI(token="token"), "create_role_template", ({"role_name": "auto_role", "display_name": "自动角色"},), {}, "POST", "/api/v1/admin/role-templates", None, {"role_name": "auto_role", "display_name": "自动角色"}),
            (lambda: AdminAPI(token="token"), "get_role_template", ("auto_role",), {}, "GET", "/api/v1/admin/role-templates/auto_role", {}, None),
            (lambda: AdminAPI(token="token"), "update_role_template", ("auto_role", {"description": "updated"}), {}, "PATCH", "/api/v1/admin/role-templates/auto_role", None, {"description": "updated"}),
            (lambda: AdminAPI(token="token"), "delete_role_template", ("auto_role",), {}, "DELETE", "/api/v1/admin/role-templates/auto_role", None, None),
            (lambda: AdminAPI(token="token"), "list_role_template_permission_names", ("auto_role",), {}, "GET", "/api/v1/admin/role-templates/auto_role/permission-names", {}, None),
            (lambda: AdminAPI(token="token"), "list_quant_accounts", (), {"exchange": "binance", "status": 1}, "GET", "/api/v1/admin/quant-accounts", {"exchange": "binance", "status": 1}, None),
            (lambda: AdminAPI(token="token"), "create_quant_account", ({"exchange": "binance", "email": "auto@example.com", "api_key": "key", "secret_key": "secret"},), {}, "POST", "/api/v1/admin/quant-accounts", None, {"exchange": "binance", "email": "auto@example.com", "api_key": "key", "secret_key": "secret"}),
            (lambda: AdminAPI(token="token"), "get_quant_account", (9,), {}, "GET", "/api/v1/admin/quant-accounts/9", {}, None),
            (lambda: AdminAPI(token="token"), "update_quant_account", (9, {"status": 1}), {}, "PATCH", "/api/v1/admin/quant-accounts/9", None, {"status": 1}),
            (lambda: AdminAPI(token="token"), "delete_quant_account", (9,), {}, "DELETE", "/api/v1/admin/quant-accounts/9", None, None),
            (lambda: AdminAPI(token="token"), "update_quant_account_assets", (9, "100.12"), {}, "PATCH", "/api/v1/admin/quant-accounts/9/assets", None, {"total_assets_usdt": "100.12"}),
            (lambda: AdminAPI(token="token"), "get_quant_account_info", (9,), {"account_type": "spot"}, "GET", "/api/v1/admin/quant-accounts/9/account-info", {"account_type": "spot"}, None),
            (lambda: AdminAPI(token="token"), "query_exchange_account", ({"exchange": "binance", "api_key": "key", "secret_key": "secret"},), {}, "POST", "/api/v1/admin/exchange/account", None, {"exchange": "binance", "api_key": "key", "secret_key": "secret"}),
            (lambda: AdminAPI(token="token"), "create_admin", ({"email": "admin@example.com", "password": "Pwd123456", "display_name": "Admin"},), {}, "POST", "/api/v1/admin/admins", None, {"email": "admin@example.com", "password": "Pwd123456", "display_name": "Admin"}),
            (lambda: AdminAPI(token="token"), "reset_admin_password", (7, "NewPwd123456"), {}, "PATCH", "/api/v1/admin/admins/7/password", None, {"new_password": "NewPwd123456"}),
            (lambda: AdminAPI(token="token"), "delete_factor_evaluation_standard", (5,), {}, "DELETE", "/api/v1/admin/factor-evaluation-standards/5", None, None),
            (lambda: AdminAPI(token="token"), "update_factor_evaluation_standard", (5, {"time_window": "1d"}), {}, "PUT", "/api/v1/admin/factor-evaluation-standards/5", None, {"time_window": "1d"}),
            (lambda: AdminAPI(token="token"), "create_factor_evaluation_standard", ({"time_window": "1d"},), {}, "POST", "/api/v1/admin/factor-evaluation-standards", None, {"time_window": "1d"}),
            (lambda: AdminAPI(token="token"), "unlock_user", ("auto@example.com",), {}, "POST", "/api/v1/admin/users/unlock", None, {"email": "auto@example.com"}),
        ],
    )
    def test_api_wrapper_routes(
        self,
        monkeypatch,
        api_factory,
        call_name,
        args,
        kwargs,
        expected_method,
        expected_path,
        expected_params,
        expected_json,
    ):
        """验证 raw API wrapper 方法、路径、查询参数和 body 归属正确。

        请求参数:
            monkeypatch: pytest 提供的替换工具。
            api_factory: 当前用例要实例化的 API wrapper 工厂。
            call_name: 当前用例要调用的 wrapper 方法名。
            args: 传给 wrapper 方法的位置参数。
            kwargs: 传给 wrapper 方法的关键字参数。
            expected_method: 期望 HTTP 方法。
            expected_path: 期望 URL 路径后缀。
            expected_params: 期望查询参数。
            expected_json: 期望 JSON body。
        返回值:
            无返回值；捕获到的底层 HTTP 请求应与接口文档路由一致。
        """
        calls = []

        def fake_request(method, url, **request_kwargs):
            """捕获 HTTPClient.request 入参并返回占位响应。

            请求参数:
                method: wrapper 传入的 HTTP 方法。
                url: wrapper 拼接后的完整 URL。
                **request_kwargs: wrapper 传入的 headers、params 和 json。
            返回值:
                None，占位即可；本测试只断言请求路由。
            """
            calls.append({"method": method, "url": url, **request_kwargs})

        monkeypatch.setattr(HTTPClient, "request", fake_request)

        api = api_factory()
        getattr(api, call_name)(*args, **kwargs)

        assert len(calls) == 1
        assert calls[0]["method"] == expected_method
        assert calls[0]["url"].endswith(expected_path)
        if expected_params is not None:
            assert calls[0].get("params") == expected_params
            for key, expected_value in expected_params.items():
                actual_value = calls[0]["params"].get(key)
                if type(expected_value) is int:
                    assert type(actual_value) is int
        if expected_json is not None:
            assert calls[0].get("json") == expected_json

    def test_notify_factor_result_sends_webhook_secret_header_when_configured(self, monkeypatch):
        """验证因子挖掘通知接口配置 webhook secret 时会发送签名请求头。

        请求参数:
            monkeypatch: pytest 提供的替换工具，用于设置 settings.factor_webhook_secret 并捕获底层请求。
        返回值:
            无返回值；捕获到的请求 headers 应包含 X-Webhook-Secret。
        """
        calls = []

        def fake_request(method, url, **request_kwargs):
            """捕获 HTTPClient.request 入参并返回占位响应。

            请求参数:
                method: wrapper 传入的 HTTP 方法。
                url: wrapper 拼接后的完整 URL。
                **request_kwargs: wrapper 传入的 headers、params 和 json。
            返回值:
                None，占位即可；本测试只断言通知接口请求头。
            """
            calls.append({"method": method, "url": url, **request_kwargs})

        monkeypatch.setattr(HTTPClient, "request", fake_request)
        monkeypatch.setattr("api.platform.factor_api.settings.factor_webhook_secret", "secret-1", raising=False)

        FactorAPI(token="token").notify_factor_result("run-1")

        assert calls[0]["headers"]["X-Webhook-Secret"] == "secret-1"
