"""因子库 Admin 模块原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from service.common.http.http_client import HTTPClient


class AdminAPI:
    """Admin 接口原始请求封装。

    请求参数:
        实例化时可传入 token，用于访问需要管理员权限的接口。
    返回值:
        提供 Admin HTTP 请求方法的 API 客户端实例。
    """

    def __init__(self, token: str | None = None):
        """初始化 Admin API 客户端。

        请求参数:
            token: 可选 JWT token；传入后自动写入 Authorization header。
        返回值:
            无，实例化后保存 base_url 和默认请求头。
        """
        self.base_url = settings.base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, params: dict[str, Any] | None = None):
        """发送 GET 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=self.clean_params(params))

    def post(self, endpoint: str, json: dict[str, Any] | None = None):
        """发送 POST 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json)

    def put(self, endpoint: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None):
        """发送 PUT 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
            params: 查询参数字典，值为 None 的字段会被过滤。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        kwargs: dict[str, Any] = {"headers": self.headers}
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = self.clean_params(params)
        return HTTPClient.request("PUT", url, **kwargs)

    def patch(self, endpoint: str, json: dict[str, Any] | None = None):
        """发送 PATCH 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
            json: 请求 JSON body。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        kwargs: dict[str, Any] = {"headers": self.headers}
        if json is not None:
            kwargs["json"] = json
        return HTTPClient.request("PATCH", url, **kwargs)

    def delete(self, endpoint: str):
        """发送 DELETE 请求。

        请求参数:
            endpoint: 以 / 开头的接口路径。
        返回值:
            requests.Response 对象；HTTPClient 会对 4xx/5xx 抛出 HTTPError。
        """
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("DELETE", url, headers=self.headers)

    def list_users(self, **params: Any):
        """调用用户列表接口。

        请求参数:
            **params: status 等查询参数。
        返回值:
            用户列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/users", params)

    def update_user(self, user_id: Any, payload: dict[str, Any]):
        """调用更新用户接口。

        请求参数:
            user_id: 用户 ID。
            payload: 更新用户的 JSON body。
        返回值:
            更新用户接口 requests.Response 对象。
        """
        return self.patch(f"/api/v1/admin/users/{user_id}", json=payload)

    def delete_user(self, user_id: Any):
        """调用删除用户接口。

        请求参数:
            user_id: 用户 ID。
        返回值:
            删除用户接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/admin/users/{user_id}")

    def unlock_user(self, email: str):
        """调用解锁用户接口。

        请求参数:
            email: 待解锁用户邮箱。
        返回值:
            解锁用户接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/users/unlock", json={"email": email})

    def get_user_permissions(self, user_id: Any):
        """调用获取用户显式权限接口。

        请求参数:
            user_id: 用户 ID。
        返回值:
            获取用户显式权限接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/admin/users/{user_id}/permissions")

    def replace_user_permissions(self, user_id: Any, perm_codes: list[str]):
        """调用替换用户显式权限接口。

        请求参数:
            user_id: 用户 ID。
            perm_codes: 权限 code 列表。
        返回值:
            替换用户显式权限接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/admin/users/{user_id}/permissions", json={"perm_codes": perm_codes})

    def grant_user_permission(self, user_id: Any, code: str):
        """调用授予用户单个权限接口。

        请求参数:
            user_id: 用户 ID。
            code: 权限 code。
        返回值:
            授予用户单个权限接口 requests.Response 对象。
        """
        return self.post(f"/api/v1/admin/users/{user_id}/permissions/{code}")

    def revoke_user_permission(self, user_id: Any, code: str):
        """调用撤销用户单个权限接口。

        请求参数:
            user_id: 用户 ID。
            code: 权限 code。
        返回值:
            撤销用户单个权限接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/admin/users/{user_id}/permissions/{code}")

    def list_invite_codes(self):
        """调用邀请码列表接口。

        请求参数:
            无。
        返回值:
            邀请码列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/invite-codes")

    def list_permissions(self):
        """调用权限定义列表接口。

        请求参数:
            无。
        返回值:
            权限定义列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/permissions")

    def list_role_templates(self):
        """调用角色模板列表接口。

        请求参数:
            无。
        返回值:
            角色模板列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/role-templates")

    def create_role_template(self, payload: dict[str, Any]):
        """调用创建角色模板接口。

        请求参数:
            payload: 创建角色模板的 JSON body。
        返回值:
            创建角色模板接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/role-templates", json=payload)

    def get_role_template(self, role_name: str):
        """调用角色模板详情接口。

        请求参数:
            role_name: 角色模板名称。
        返回值:
            角色模板详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/admin/role-templates/{role_name}")

    def update_role_template(self, role_name: str, payload: dict[str, Any]):
        """调用更新角色模板接口。

        请求参数:
            role_name: 角色模板名称。
            payload: 更新角色模板的 JSON body。
        返回值:
            更新角色模板接口 requests.Response 对象。
        """
        return self.patch(f"/api/v1/admin/role-templates/{role_name}", json=payload)

    def delete_role_template(self, role_name: str):
        """调用删除角色模板接口。

        请求参数:
            role_name: 角色模板名称。
        返回值:
            删除角色模板接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/admin/role-templates/{role_name}")

    def list_role_template_permission_names(self, role_name: str):
        """调用角色模板权限显示名列表接口。

        请求参数:
            role_name: 角色模板名称。
        返回值:
            角色模板权限显示名列表接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/admin/role-templates/{role_name}/permission-names")

    def list_quant_accounts(self, **params: Any):
        """调用量化账户列表接口。

        请求参数:
            **params: exchange、admin_id、status、search 等查询参数。
        返回值:
            量化账户列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/quant-accounts", params)

    def create_quant_account(self, payload: dict[str, Any]):
        """调用创建量化账户接口。

        请求参数:
            payload: 创建量化账户的 JSON body。
        返回值:
            创建量化账户接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/quant-accounts", json=payload)

    def get_quant_account(self, account_id: Any):
        """调用量化账户详情接口。

        请求参数:
            account_id: 量化账户 ID。
        返回值:
            量化账户详情接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/admin/quant-accounts/{account_id}")

    def update_quant_account(self, account_id: Any, payload: dict[str, Any]):
        """调用更新量化账户接口。

        请求参数:
            account_id: 量化账户 ID。
            payload: 更新量化账户的 JSON body。
        返回值:
            更新量化账户接口 requests.Response 对象。
        """
        return self.patch(f"/api/v1/admin/quant-accounts/{account_id}", json=payload)

    def delete_quant_account(self, account_id: Any):
        """调用删除量化账户接口。

        请求参数:
            account_id: 量化账户 ID。
        返回值:
            删除量化账户接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/admin/quant-accounts/{account_id}")

    def update_quant_account_assets(self, account_id: Any, total_assets_usdt: Any):
        """调用更新量化账户总资产接口。

        请求参数:
            account_id: 量化账户 ID。
            total_assets_usdt: 总资产 USDT 值。
        返回值:
            更新量化账户总资产接口 requests.Response 对象。
        """
        return self.patch(
            f"/api/v1/admin/quant-accounts/{account_id}/assets",
            json={"total_assets_usdt": total_assets_usdt},
        )

    def get_quant_account_info(self, account_id: Any, account_type: Any = None):
        """调用实时交易所账户信息接口。

        请求参数:
            account_id: 量化账户 ID。
            account_type: 账户类型，支持 spot、futures、all。
        返回值:
            实时交易所账户信息接口 requests.Response 对象。
        """
        return self.get(f"/api/v1/admin/quant-accounts/{account_id}/account-info", {"account_type": account_type})

    def query_exchange_account(self, payload: dict[str, Any]):
        """调用直接凭证查询交易所账户接口。

        请求参数:
            payload: exchange、api_key、secret_key、passphrase、account_type 等 JSON body。
        返回值:
            直接凭证查询交易所账户接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/exchange/account", json=payload)

    def create_admin(self, payload: dict[str, Any]):
        """调用创建管理员接口。

        请求参数:
            payload: 创建管理员的 JSON body。
        返回值:
            创建管理员接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/admins", json=payload)

    def reset_admin_password(self, admin_id: Any, new_password: str):
        """调用重置管理员密码接口。

        请求参数:
            admin_id: 管理员 ID。
            new_password: 新密码。
        返回值:
            重置管理员密码接口 requests.Response 对象。
        """
        return self.patch(f"/api/v1/admin/admins/{admin_id}/password", json={"new_password": new_password})

    def update_agent_factory_config(self, payload: dict[str, Any], coin_category: Any = None):
        """调用更新 Agent Factory 配置接口。

        请求参数:
            payload: Agent Factory 配置 JSON body。
            coin_category: 可选币种分类查询参数。
        返回值:
            更新 Agent Factory 配置接口 requests.Response 对象。
        """
        return self.put("/api/v1/admin/agent-factory-config", json=payload, params={"coin_category": coin_category})

    def delete_factor_evaluation_standard(self, standard_id: Any):
        """调用删除因子评价标准接口。

        请求参数:
            standard_id: 因子评价标准 ID。
        返回值:
            删除因子评价标准接口 requests.Response 对象。
        """
        return self.delete(f"/api/v1/admin/factor-evaluation-standards/{standard_id}")

    def update_factor_evaluation_standard(self, standard_id: Any, payload: dict[str, Any]):
        """调用更新因子评价标准接口。

        请求参数:
            standard_id: 因子评价标准 ID。
            payload: 更新因子评价标准的 JSON body。
        返回值:
            更新因子评价标准接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/admin/factor-evaluation-standards/{standard_id}", json=payload)

    def create_factor_evaluation_standard(self, payload: dict[str, Any]):
        """调用创建因子评价标准接口。

        请求参数:
            payload: 创建因子评价标准的 JSON body。
        返回值:
            创建因子评价标准接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/factor-evaluation-standards", json=payload)

    def list_prompts(self, **params: Any):
        """调用提示词列表接口。

        请求参数:
            **params: used_by、type、name、limit 等查询参数。
        返回值:
            提示词列表接口 requests.Response 对象。
        """
        return self.get("/api/v1/admin/prompts", params)

    def create_prompt(self, payload: dict[str, Any]):
        """调用创建提示词接口。

        请求参数:
            payload: 创建提示词的 JSON body。
        返回值:
            创建提示词接口 requests.Response 对象。
        """
        return self.post("/api/v1/admin/prompts", json=payload)

    def update_prompt(self, prompt_id: Any, payload: dict[str, Any]):
        """调用更新提示词接口。

        请求参数:
            prompt_id: 提示词 ID。
            payload: 更新提示词的 JSON body。
        返回值:
            更新提示词接口 requests.Response 对象。
        """
        return self.put(f"/api/v1/admin/prompts/{prompt_id}", json=payload)

    @staticmethod
    def clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
        """过滤查询参数中的 None 值。

        请求参数:
            params: 原始查询参数字典。
        返回值:
            去掉 None 值后的查询参数字典；输入为空时返回空字典。
        """
        if not params:
            return {}
        return {key: value for key, value in params.items() if value is not None}
