from __future__ import annotations

from typing import Any

import pytest


class FactorTestDataService:
    """factor 模块自动化测试数据组装服务。

    请求参数:
        不需要实例化，直接通过静态方法从现有接口数据派生创建依赖并组装 payload。
    返回值:
        提供主题、母因子依赖派生和创建 payload 构造能力。
    """

    @staticmethod
    def build_factor_payload(factor_resource_api: Any, test_data_factory: Any, case_id: str) -> dict[str, Any]:
        """构造可成功创建母因子的请求 body。

        请求参数:
            factor_resource_api: factor 模块 API 客户端，需提供 list_themes 方法。
            test_data_factory: 自动化测试数据工厂，需提供 name 方法。
            case_id: 当前用例编号或场景标识。
        返回值:
            带 serial_prefix、factor_name、cn_name、theme_id 等字段的创建因子 payload。
        """
        name = test_data_factory.name("factor", case_id)
        return {
            "serial_prefix": "AUTO",
            "theme_id": FactorTestDataService.first_theme_id(factor_resource_api),
            "factor_name": name,
            "cn_name": name,
            "formula_summary": "auto factor",
            "factor_tags": "auto",
            "level": 1,
            "metadata": {"source": "api_test"},
        }

    @staticmethod
    def build_sub_factor_payload(factor_resource_api: Any, test_data_factory: Any, case_id: str) -> dict[str, Any]:
        """构造可成功创建二级子因子的请求 body。

        请求参数:
            factor_resource_api: factor 模块 API 客户端，需提供 list_factors 方法。
            test_data_factory: 自动化测试数据工厂，需提供 name 方法。
            case_id: 当前用例编号或场景标识。
        返回值:
            带 serial_prefix、sub_factor_name、factor_id、level 等字段的创建子因子 payload。
        """
        name = test_data_factory.name("sub_factor", case_id)
        return {
            "serial_prefix": "AUTO",
            "factor_id": FactorTestDataService.first_factor_id(factor_resource_api),
            "sub_factor_name": name,
            "cn_name": name,
            "level": 2,
            "window": "24",
            "window_value": "24",
            "window_unit": "1h",
            "formula_summary": "auto sub factor",
            "sub_factor_tags": "auto",
            "metadata": {"source": "api_test"},
        }

    @staticmethod
    def build_theme_payload(test_data_factory: Any, case_id: str) -> dict[str, Any]:
        """构造可成功创建主题的请求 body。

        请求参数:
            test_data_factory: 自动化测试数据工厂，需提供 name 方法。
            case_id: 当前用例编号或场景标识。
        返回值:
            带 theme_key、theme_name、cn_name 和 theme_tags 的主题创建 payload。
        """
        name = test_data_factory.name("theme", case_id)
        return {"theme_key": name, "theme_name": name, "cn_name": name, "theme_tags": "auto"}

    @staticmethod
    def build_level_three_sub_factor_payload(
        test_data_factory: Any,
        case_id: str,
        parent_sub_factor_ids: list[int],
        factor_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """构造三级及以上子因子的创建 body。

        请求参数:
            test_data_factory: 自动化测试数据工厂，需提供 name 方法。
            case_id: 当前用例编号或场景标识。
            parent_sub_factor_ids: 父级子因子 ID 列表。
            factor_ids: 可选母因子 ID 列表，必须是父级已关联母因子的子集。
        返回值:
            带 parent_sub_factor_ids、level=3 的创建子因子 payload。
        """
        name = test_data_factory.name("sub_factor_l3", case_id)
        payload: dict[str, Any] = {
            "serial_prefix": "AUTO",
            "sub_factor_name": name,
            "cn_name": name,
            "level": 3,
            "parent_sub_factor_ids": parent_sub_factor_ids,
            "window": "24",
            "window_value": "24",
            "window_unit": "1h",
            "formula_summary": "auto sub factor level 3",
            "sub_factor_tags": "auto",
            "metadata": {"source": "api_test"},
        }
        if factor_ids is not None:
            payload["factor_ids"] = factor_ids
        return payload

    @staticmethod
    def first_theme_id(factor_resource_api: Any) -> int:
        """从主题列表派生一个真实主题 ID。

        请求参数:
            factor_resource_api: factor 模块 API 客户端，需提供 list_themes 方法。
        返回值:
            主题 ID；主题列表为空或首条缺少 id 时跳过当前用例。
        """
        body = factor_resource_api.list_themes().json()
        data = body.get("data")
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        if not items or not items[0].get("id"):
            pytest.skip("主题列表为空，无法派生创建因子所需 theme_id。")
        return items[0]["id"]

    @staticmethod
    def first_factor_id(factor_resource_api: Any) -> int:
        """从因子列表派生一个真实母因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API 客户端，需提供 list_factors 方法。
        返回值:
            母因子 ID；因子列表为空或首条缺少 id 时跳过当前用例。
        """
        body = factor_resource_api.list_factors(page=1, limit=1).json()
        items = body.get("data", {}).get("items", [])
        if not items or not items[0].get("id"):
            pytest.skip("因子列表为空，无法派生创建子因子所需 factor_id。")
        return items[0]["id"]

    @staticmethod
    def first_sub_factor_id(factor_resource_api: Any) -> int:
        """从子因子列表派生一个真实子因子 ID。

        请求参数:
            factor_resource_api: factor 模块 API 客户端，需提供 list_sub_factors 方法。
        返回值:
            子因子 ID；子因子列表为空或首条缺少 id 时跳过当前用例。
        """
        body = factor_resource_api.list_sub_factors(page=1, limit=1).json()
        items = body.get("data", {}).get("items", [])
        if not items or not items[0].get("id"):
            pytest.skip("子因子列表为空，无法派生 sub_factor_id。")
        return items[0]["id"]
