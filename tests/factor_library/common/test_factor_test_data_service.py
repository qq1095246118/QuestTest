import pytest

from service.factor_library.common.test_data_factory import TestDataFactory
from service.factor_library.factors.factor_test_data import FactorTestDataService


class FakeResponse:
    """内存响应对象。

    请求参数:
        body: 需要通过 json 方法返回的响应体。
    返回值:
        提供 json 方法的测试替身对象。
    """

    def __init__(self, body):
        """初始化内存响应对象。

        请求参数:
            body: 响应 JSON 字典。
        返回值:
            无，实例化后保存 body。
        """
        self.body = body

    def json(self):
        """返回内存响应 JSON。

        请求参数:
            无。
        返回值:
            初始化时传入的 body。
        """
        return self.body


class FakeFactorAPI:
    """内存 factor API 替身。

    请求参数:
        themes: 主题列表。
        factors: 因子列表。
    返回值:
        提供 list_themes 和 list_factors 方法的测试替身对象。
    """

    def __init__(self, themes=None, factors=None):
        """初始化内存 factor API 替身。

        请求参数:
            themes: list_themes 要返回的主题 items。
            factors: list_factors 要返回的因子 items。
        返回值:
            无，实例化后保存内存列表。
        """
        self.themes = themes or []
        self.factors = factors or []

    def list_themes(self, **params):
        """返回内存主题列表。

        请求参数:
            **params: 查询参数，本替身不使用。
        返回值:
            FakeResponse，data.items 为初始化时传入的 themes。
        """
        return FakeResponse({"success": True, "data": {"items": self.themes}})

    def list_factors(self, **params):
        """返回内存因子列表。

        请求参数:
            **params: 查询参数，本替身不使用。
        返回值:
            FakeResponse，data.items 为初始化时传入的 factors。
        """
        return FakeResponse({"success": True, "data": {"items": self.factors}})


class TestFactorTestDataService:
    """factor 模块测试数据服务单元测试。

    请求参数:
        使用内存 API 替身和固定 run_id 的 TestDataFactory。
    返回值:
        无返回值；pytest 根据生成 payload 判断创建依赖是否完整。
    """

    def test_build_factor_payload_includes_existing_theme_id(self):
        """验证因子创建 payload 会带上现有主题 ID。

        请求参数:
            主题列表包含 id=8。
        返回值:
            payload 应包含 theme_id=8 和 auto 因子名。
        """
        api = FakeFactorAPI(themes=[{"id": 8, "theme_key": "momentum"}])
        factory = TestDataFactory(run_id="20260610130000")

        payload = FactorTestDataService.build_factor_payload(api, factory, "fa_17")

        assert payload["theme_id"] == 8
        assert payload["factor_name"].startswith("auto_test_20260610130000_factor_fa_17_")

    def test_build_factor_payload_skips_when_no_theme_exists(self):
        """验证没有可用主题时跳过依赖正向创建的用例。

        请求参数:
            主题列表为空。
        返回值:
            pytest.skip 异常。
        """
        api = FakeFactorAPI(themes=[])
        factory = TestDataFactory(run_id="20260610130000")

        with pytest.raises(pytest.skip.Exception):
            FactorTestDataService.build_factor_payload(api, factory, "fa_17")

    def test_build_sub_factor_payload_includes_existing_factor_id(self):
        """验证子因子创建 payload 会带上现有母因子 ID。

        请求参数:
            因子列表包含 id=18。
        返回值:
            payload 应包含 factor_id=18、level=2 和 auto 子因子名。
        """
        api = FakeFactorAPI(factors=[{"id": 18, "factor_name": "factor_a"}])
        factory = TestDataFactory(run_id="20260610130000")

        payload = FactorTestDataService.build_sub_factor_payload(api, factory, "sf_04")

        assert payload["factor_id"] == 18
        assert payload["level"] == 2
        assert payload["sub_factor_name"].startswith("auto_test_20260610130000_sub_factor_sf_04_")

    def test_build_sub_factor_payload_skips_when_no_factor_exists(self):
        """验证没有可用母因子时跳过依赖正向创建的用例。

        请求参数:
            因子列表为空。
        返回值:
            pytest.skip 异常。
        """
        api = FakeFactorAPI(factors=[])
        factory = TestDataFactory(run_id="20260610130000")

        with pytest.raises(pytest.skip.Exception):
            FactorTestDataService.build_sub_factor_payload(api, factory, "sf_04")

    def test_build_theme_payload_contains_unique_theme_fields(self):
        """验证主题创建 payload 包含唯一主题字段。

        请求参数:
            固定 run_id 的 TestDataFactory。
        返回值:
            payload 应包含 theme_key、theme_name、cn_name 和 theme_tags。
        """
        factory = TestDataFactory(run_id="20260610130000")

        payload = FactorTestDataService.build_theme_payload(factory, "th_04")

        assert payload["theme_key"].startswith("auto_test_20260610130000_theme_th_04_")
        assert payload["theme_name"] == payload["theme_key"]
        assert payload["cn_name"] == payload["theme_key"]
        assert payload["theme_tags"] == "auto"

    def test_build_level_three_sub_factor_payload_contains_parent_ids(self):
        """验证三级子因子 payload 包含父级子因子关系。

        请求参数:
            parent_sub_factor_ids=[11, 12]，factor_ids=[8]。
        返回值:
            payload 应包含 level=3、parent_sub_factor_ids 和 factor_ids。
        """
        factory = TestDataFactory(run_id="20260610130000")

        payload = FactorTestDataService.build_level_three_sub_factor_payload(factory, "sf_l3", [11, 12], [8])

        assert payload["level"] == 3
        assert payload["parent_sub_factor_ids"] == [11, 12]
        assert payload["factor_ids"] == [8]
        assert payload["sub_factor_name"].startswith("auto_test_20260610130000_sub_factor_l3_sf_l3_")
