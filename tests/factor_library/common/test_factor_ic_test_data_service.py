import pytest

from service.factor_library.factor_ic.factor_ic_test_data import FactorICTestDataService


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


class FakeFactorICAPI:
    """内存 FactorIC API 替身。

    请求参数:
        summary_items: IC 汇总指标列表。
        run_items: IC run 列表。
    返回值:
        提供 list_summary_metrics 和 list_runs 方法的测试替身对象。
    """

    def __init__(self, summary_items=None, run_items=None):
        """初始化内存 FactorIC API 替身。

        请求参数:
            summary_items: list_summary_metrics 要返回的 items。
            run_items: list_runs 要返回的 items。
        返回值:
            无，实例化后保存内存列表。
        """
        self.summary_items = summary_items or []
        self.run_items = run_items or []
        self.summary_metric_calls = []

    def list_summary_metrics(self, **params):
        """返回内存 IC 汇总指标列表。

        请求参数:
            **params: 查询参数，记录后用于验证 service 传给接口的 owner 类型筛选值。
        返回值:
            FakeResponse，data.items 为初始化时传入的 summary_items。
        """
        self.summary_metric_calls.append(params)
        return FakeResponse({"success": True, "data": {"items": self.summary_items}})

    def list_runs(self, **params):
        """返回内存 IC run 列表。

        请求参数:
            **params: 查询参数，本替身不使用。
        返回值:
            FakeResponse，data.items 为初始化时传入的 run_items。
        """
        return FakeResponse({"success": True, "data": {"items": self.run_items}})


class TestFactorICTestDataService:
    """FactorIC 测试数据派生服务单元测试。

    请求参数:
        使用内存 FactorIC API 替身。
    返回值:
        无返回值；pytest 根据派生出的 ID 判断正向查询依赖是否可靠。
    """

    def test_first_factor_id_with_summary_metric_returns_non_sub_factor_id(self):
        """验证母因子 summary 正向查询从已有母因子指标派生 factor_id。

        请求参数:
            汇总指标列表同时包含母因子和子因子指标。
        返回值:
            返回母因子指标中的 factor_id。
        """
        api = FakeFactorICAPI(
            summary_items=[
                {"factor_id": 9020, "is_sub_factor_id": True},
                {"factor_id": 59, "is_sub_factor_id": False},
            ]
        )

        factor_id = FactorICTestDataService.first_factor_id_with_summary_metric(api)

        assert factor_id == 59

    def test_first_factor_id_with_summary_metric_queries_non_sub_factor_as_integer_zero(self):
        """验证母因子 summary 派生查询使用接口可识别的整数 0。

        请求参数:
            汇总指标列表包含一条母因子指标。
        返回值:
            调用 list_summary_metrics 时 is_sub_factor_id 应为整数 0，而不是 Python False。
        """
        api = FakeFactorICAPI(summary_items=[{"factor_id": 59, "is_sub_factor_id": False}])

        FactorICTestDataService.first_factor_id_with_summary_metric(api)

        assert api.summary_metric_calls[0]["is_sub_factor_id"] == 0
        assert api.summary_metric_calls[0]["is_sub_factor_id"] is not False

    def test_first_sub_factor_id_with_summary_metric_returns_sub_factor_id(self):
        """验证子因子 summary 正向查询从已有子因子指标派生 sub_factor_id。

        请求参数:
            汇总指标列表先返回母因子指标，再返回子因子指标。
        返回值:
            返回子因子指标中的 factor_id，作为 sub_factor_id 使用。
        """
        api = FakeFactorICAPI(
            summary_items=[
                {"factor_id": 59, "is_sub_factor_id": False},
                {"factor_id": 9020, "is_sub_factor_id": True},
            ]
        )

        sub_factor_id = FactorICTestDataService.first_sub_factor_id_with_summary_metric(api)

        assert sub_factor_id == 9020

    def test_first_sub_factor_id_with_summary_metric_queries_sub_factor_as_integer_one(self):
        """验证子因子 summary 派生查询使用接口可识别的整数 1。

        请求参数:
            汇总指标列表包含一条子因子指标。
        返回值:
            调用 list_summary_metrics 时 is_sub_factor_id 应为整数 1，而不是 Python True。
        """
        api = FakeFactorICAPI(summary_items=[{"factor_id": 9020, "is_sub_factor_id": True}])

        FactorICTestDataService.first_sub_factor_id_with_summary_metric(api)

        assert api.summary_metric_calls[0]["is_sub_factor_id"] == 1
        assert api.summary_metric_calls[0]["is_sub_factor_id"] is not True

    def test_first_run_id_returns_business_run_id_field(self):
        """验证 IC run 详情使用业务 run_id 字段。

        请求参数:
            run 列表同时包含 id 和 run_id。
        返回值:
            返回 run_id 字符串，不返回数据库 id。
        """
        api = FakeFactorICAPI(run_items=[{"id": 13919, "run_id": "run_business_id"}])

        run_id = FactorICTestDataService.first_run_id(api)

        assert run_id == "run_business_id"

    def test_first_run_id_skips_when_no_run_exists(self):
        """验证没有 IC run 时跳过依赖 run 详情的正向用例。

        请求参数:
            run 列表为空。
        返回值:
            pytest.skip 异常。
        """
        api = FakeFactorICAPI(run_items=[])

        with pytest.raises(pytest.skip.Exception):
            FactorICTestDataService.first_run_id(api)

    def test_build_run_payload_uses_factor_id_and_auto_test_window(self):
        """验证 IC run 创建 payload 使用测试因子和稳定窗口。

        请求参数:
            factor_id=101，case_id=ic_15。
        返回值:
            payload 应包含 factor_id、ic_scope、time_window 和 coin_category。
        """
        payload = FactorICTestDataService.build_run_payload(101, "ic_15")

        assert payload == {
            "run_id": "auto_test_ic_15_101",
            "factor_id": 101,
            "is_sub_factor_id": False,
            "ic_scope": "time_series",
            "time_window": "1d",
            "coin_category": "main",
        }

    def test_build_summary_metric_item_contains_unique_run_and_factor_dimensions(self):
        """验证 IC 汇总指标写入 item 包含唯一 run 和因子维度。

        请求参数:
            run_id=auto_test_run，factor_id=101。
        返回值:
            item 应包含 upsert 所需维度字段和指标字段。
        """
        item = FactorICTestDataService.build_summary_metric_item("auto_test_run", 101)

        assert item["run_id"] == "auto_test_run"
        assert item["factor_id"] == 101
        assert item["is_sub_factor_id"] is False
        assert item["window_scope"] == "full"
        assert item["metrics"]["mean_ic"] == 0.12
        assert item["period_start"] == "2026-01-01T00:00:00Z"
        assert item["mean_ic"] == 0.12

    def test_build_slice_metric_item_contains_unique_run_and_symbol_dimensions(self):
        """验证 IC 切片指标写入 item 包含唯一 run 和 symbol 维度。

        请求参数:
            run_id=auto_test_run，factor_id=101。
        返回值:
            item 应包含 upsert 所需维度字段、时间切片和指标字段。
        """
        item = FactorICTestDataService.build_slice_metric_item("auto_test_run", 101)

        assert item["run_id"] == "auto_test_run"
        assert item["factor_id"] == 101
        assert item["symbol"] == "BTCUSDT"
        assert item["metrics"]["ic"] == 0.12
        assert item["slice_start"] == "2026-01-01T00:00:00Z"
        assert item["ic"] == 0.12

    def test_build_slice_metric_item_contains_v2_required_dimensions(self):
        """验证切片指标 item 包含新版唯一键维度字段。

        请求参数:
            run_id=auto_run，factor_id=123。
        返回值:
            item 应包含 run_id、factor_id、is_sub_factor_id、ic_scope、calculation_mode、window 和 slice 时间字段。
        """
        item = FactorICTestDataService.build_slice_metric_item("auto_run", 123)

        for key in [
            "run_id",
            "factor_id",
            "is_sub_factor_id",
            "ic_scope",
            "calculation_mode",
            "factor_bar_interval",
            "factor_window_bars",
            "return_bar_interval",
            "forward_return_bars",
            "universe_key",
            "symbol",
            "window_scope",
            "sample_segment",
            "slice_start",
            "slice_end",
        ]:
            assert key in item
