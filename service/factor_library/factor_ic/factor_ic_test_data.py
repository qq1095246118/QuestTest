from __future__ import annotations

from typing import Any

import pytest


class FactorICTestDataService:
    """FactorIC 自动化测试数据派生服务。

    请求参数:
        不需要实例化，直接通过静态方法从只读 IC 列表接口派生正向详情查询所需 ID。
    返回值:
        提供可用于 IC summary 和 run 详情正向查询的业务 ID。
    """

    @staticmethod
    def first_factor_id_with_summary_metric(factor_ic_api: Any) -> int:
        """从已有母因子 IC 汇总指标中派生 factor_id。

        请求参数:
            factor_ic_api: FactorIC API 客户端，需提供 list_summary_metrics 方法。
        返回值:
            有 IC 汇总指标的母因子 ID；没有可用数据时跳过当前用例。
        """
        item = FactorICTestDataService.first_summary_metric_item(factor_ic_api, is_sub_factor_id=False)
        return item["factor_id"]

    @staticmethod
    def first_sub_factor_id_with_summary_metric(factor_ic_api: Any) -> int:
        """从已有子因子 IC 汇总指标中派生 sub_factor_id。

        请求参数:
            factor_ic_api: FactorIC API 客户端，需提供 list_summary_metrics 方法。
        返回值:
            有 IC 汇总指标的子因子 ID；没有可用数据时跳过当前用例。
        """
        item = FactorICTestDataService.first_summary_metric_item(factor_ic_api, is_sub_factor_id=True)
        return item["factor_id"]

    @staticmethod
    def first_summary_metric_item(factor_ic_api: Any, is_sub_factor_id: bool) -> dict[str, Any]:
        """按 owner 类型读取首条 IC 汇总指标。

        请求参数:
            factor_ic_api: FactorIC API 客户端，需提供 list_summary_metrics 方法。
            is_sub_factor_id: False 表示母因子指标，True 表示子因子指标。
        返回值:
            首条符合 owner 类型的汇总指标字典；没有可用数据时跳过当前用例。
        """
        body = factor_ic_api.list_summary_metrics(is_sub_factor_id=int(is_sub_factor_id), limit=20).json()
        items = FactorICTestDataService.extract_items(body)
        for item in items:
            if bool(item.get("is_sub_factor_id")) is is_sub_factor_id and item.get("factor_id"):
                return item

        owner_name = "子因子" if is_sub_factor_id else "母因子"
        pytest.skip(f"IC 汇总指标列表中没有可用{owner_name}指标，无法派生正向查询 ID。")

    @staticmethod
    def first_run_id(factor_ic_api: Any) -> str:
        """从 IC run 列表派生业务 run_id。

        请求参数:
            factor_ic_api: FactorIC API 客户端，需提供 list_runs 方法。
        返回值:
            IC run 的业务 run_id 字符串；没有可用数据时跳过当前用例。
        """
        body = factor_ic_api.list_runs(limit=1).json()
        items = FactorICTestDataService.extract_items(body)
        if not items or not items[0].get("run_id"):
            pytest.skip("IC runs 列表为空或首条缺少 run_id，无法派生 run 详情查询 ID。")
        return items[0]["run_id"]

    @staticmethod
    def build_run_payload(factor_id: int, case_id: str, is_sub_factor_id: bool = False) -> dict[str, Any]:
        """构造 IC run 创建 payload。

        请求参数:
            factor_id: 本次自动化创建的因子或子因子 ID。
            case_id: 当前用例编号或场景标识，用于保留语义。
            is_sub_factor_id: True 表示 factor_id 指向子因子，False 表示母因子。
        返回值:
            创建 IC run 接口 JSON body。
        """
        return {
            "run_id": f"auto_test_{case_id}_{factor_id}",
            "factor_id": factor_id,
            "is_sub_factor_id": is_sub_factor_id,
            "ic_scope": "time_series",
            "time_window": "1d",
            "coin_category": "main",
        }

    @staticmethod
    def build_summary_metric_item(run_id: str, factor_id: int, is_sub_factor_id: bool = False) -> dict[str, Any]:
        """构造批量 upsert IC 汇总指标 item。

        请求参数:
            run_id: 当前自动化运行生成或指定的 IC run 业务 ID。
            factor_id: 本次自动化创建的因子或子因子 ID。
            is_sub_factor_id: True 表示 factor_id 指向子因子，False 表示母因子。
        返回值:
            可提交到 summary-metrics batch 接口的单条指标字典。
        """
        return {
            "run_id": run_id,
            "factor_id": factor_id,
            "is_sub_factor_id": is_sub_factor_id,
            "ic_scope": "time_series",
            "calculation_mode": "direct",
            "factor_bar_interval": "1d",
            "factor_window_bars": "1",
            "return_bar_interval": "1d",
            "forward_return_bars": 1,
            "universe_key": "main",
            "symbol": "BTCUSDT",
            "window_scope": "full",
            "metric_window_bars": 30,
            "metric_window_days": 30,
            "period_start": "2026-01-01T00:00:00Z",
            "period_end": "2026-01-31T00:00:00Z",
            "slice_count": 3,
            "valid_slice_count": 3,
            "coverage_mean": 1.0,
            "mean_ic": 0.12,
            "median_ic": 0.12,
            "std_ic": 0.01,
            "icir": 1.2,
            "mean_abs_ic": 0.12,
            "positive_ic_rate": 1.0,
            "mean_rank_ic": 0.11,
            "rank_icir": 1.1,
            "ic_t_stat": 2.1,
            "final_score": 80.0,
            "metrics": {"mean_ic": 0.12, "mean_rank_ic": 0.11, "icir": 1.2, "source": "auto_test"},
            "metrics_json": {"source": "auto_test"},
        }

    @staticmethod
    def build_slice_metric_item(run_id: str, factor_id: int, is_sub_factor_id: bool = False) -> dict[str, Any]:
        """构造批量 upsert IC 切片指标 item。

        请求参数:
            run_id: 当前自动化运行生成或指定的 IC run 业务 ID。
            factor_id: 本次自动化创建的因子或子因子 ID。
            is_sub_factor_id: True 表示 factor_id 指向子因子，False 表示母因子。
        返回值:
            可提交到 slice-metrics batch 接口的单条指标字典。
        """
        return {
            "run_id": run_id,
            "factor_id": factor_id,
            "is_sub_factor_id": is_sub_factor_id,
            "ic_scope": "time_series",
            "calculation_mode": "direct",
            "factor_bar_interval": "1d",
            "factor_window_bars": "1",
            "return_bar_interval": "1d",
            "forward_return_bars": 1,
            "universe_key": "main",
            "symbol": "BTCUSDT",
            "window_scope": "full",
            "metric_window_bars": 30,
            "metric_window_days": 30,
            "sample_segment": "full",
            "slice_start": "2026-01-01T00:00:00Z",
            "slice_end": "2026-01-02T00:00:00Z",
            "as_of_time": "2026-01-02T00:00:00Z",
            "sample_count": 30,
            "coverage": 1.0,
            "ic": 0.12,
            "rank_ic": 0.11,
            "ic_abs": 0.12,
            "rank_ic_abs": 0.11,
            "ic_t_stat": 2.1,
            "rank_ic_t_stat": 2.0,
            "icir": 1.2,
            "rank_icir": 1.1,
            "slice_score": 80.0,
            "metrics": {"ic": 0.12, "rank_ic": 0.11, "icir": 1.2, "source": "auto_test"},
            "metrics_json": {"source": "auto_test"},
        }

    @staticmethod
    def extract_items(body: Any) -> list[dict[str, Any]]:
        """从列表响应中提取 items。

        请求参数:
            body: 接口 JSON 响应体，兼容 data 为列表或 data.items 为列表两种结构。
        返回值:
            items 列表；响应结构不匹配时返回空列表。
        """
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return []
