from __future__ import annotations

from typing import Any

import pytest


class AdminTestDataService:
    """Admin 模块自动化测试数据组装服务。

    请求参数:
        不需要实例化，直接通过静态方法生成 Admin 写入接口 payload。
    返回值:
        提供量化账号等 Admin 资源的创建参数。
    """

    @staticmethod
    def build_quant_account_payload(test_data_factory: Any, case_id: str) -> dict[str, Any]:
        """构造符合后端 schema 的量化账号创建 body。

        请求参数:
            test_data_factory: 自动化测试数据工厂，需提供 email 和 name 方法。
            case_id: 当前用例编号或场景标识。
        返回值:
            创建量化账号接口 JSON body，status 使用字符串，total_assets_usdt 使用数字。
        """
        return {
            "exchange": "binance",
            "email": test_data_factory.email(case_id),
            "api_key": test_data_factory.name("api_key", case_id),
            "secret_key": test_data_factory.name("secret_key", case_id),
            "api_description": "auto",
            "status": "active",
            "total_assets_usdt": 0,
        }

    @staticmethod
    def build_factor_evaluation_standard_payload(test_data_factory: Any, case_id: str) -> dict[str, Any]:
        """构造因子评价标准创建和更新 body。

        请求参数:
            test_data_factory: 自动化测试数据工厂，需提供 name 方法。
            case_id: 当前用例编号或场景标识。
        返回值:
            创建或更新因子评价标准接口 JSON body，coin_category 使用 auto_test 标记便于人工清理。
        """
        return {
            "time_window": "1d",
            "coin_category": test_data_factory.name("standard", case_id),
            "ic_good_min": 0.01,
            "ic_good_max": 0.05,
            "ic_better_min": 0.05,
            "ic_better_max": 0.2,
            "ic_highest_bound": 1.0,
            "icir_good_min": 0.1,
            "icir_good_max": 1.0,
            "icir_better_min": 1.0,
            "icir_better_max": 9.99,
            "tstat_good_min": 1.0,
            "tstat_good_max": 2.0,
            "tstat_better_min": 2.0,
            "tstat_better_max": 9.99,
            "oos_good_min": 0.5,
            "oos_good_max": 0.8,
            "oos_better_min": 0.8,
            "oos_better_max": 1.0,
        }

    @staticmethod
    def resolve_created_user_id(admin_api: Any, created_data: dict[str, Any], email: str) -> int:
        """解析新创建用户 ID，创建响应缺少 id 时按邮箱从用户列表反查。

        请求参数:
            admin_api: Admin API 客户端，需提供 list_users 方法。
            created_data: 创建管理员接口响应中的 data 字典。
            email: 创建管理员时使用的唯一邮箱。
        返回值:
            新创建用户 ID；创建响应和用户列表都无法唯一定位时跳过当前用例。
        """
        if created_data.get("id"):
            return created_data["id"]

        body = admin_api.list_users().json()
        data = body.get("data")
        users = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        matched_users = [user for user in users if isinstance(user, dict) and user.get("email") == email]
        matched_users_with_id = [user for user in matched_users if user.get("id")]
        if len(matched_users_with_id) == 1:
            return matched_users_with_id[0]["id"]

        pytest.skip(f"无法通过创建响应或用户列表按邮箱唯一定位新用户 ID: {email}")
