import pytest

from service.factor_library.factors.factor_mining_queries import FactorMiningDBService


class FakeDBClient:
    """因子挖掘 DB 查询测试替身。

    请求参数:
        row: fetch_one 需要返回的行数据。
    返回值:
        提供 fetch_one 方法的内存对象。
    """

    def __init__(self, row):
        """保存内存行数据。

        请求参数:
            row: 模拟 DB 查询返回行。
        返回值:
            无，实例化后保存 row 和 sql。
        """
        self.row = row
        self.sql = None

    def fetch_one(self, sql, params=None):
        """模拟只读 DB fetch_one。

        请求参数:
            sql: service 传入的 SQL。
            params: 可选查询参数。
        返回值:
            初始化时传入的 row。
        """
        self.sql = sql
        return self.row


class TestFactorMiningDBService:
    """因子挖掘 DB 查询服务单元测试。

    请求参数:
        使用 FakeDBClient 模拟只读 DB 查询。
    返回值:
        无返回值；pytest 根据 run_id 派生结果判断服务行为。
    """

    def test_first_selected_run_id_returns_existing_run_id(self):
        """验证可从已有 selected 挖掘结果中派生 run_id。

        请求参数:
            DB 返回 run_id=run_1。
        返回值:
            service 应返回 run_1，并且 SQL 只读。
        """
        client = FakeDBClient({"run_id": "run_1"})

        run_id = FactorMiningDBService.first_selected_run_id(client)

        assert run_id == "run_1"
        assert "SELECT run_id" in client.sql
        assert "factor_mining_details" in client.sql

    def test_first_selected_run_id_skips_when_no_selected_run_exists(self):
        """验证没有 selected 挖掘结果时跳过正向通知用例。

        请求参数:
            DB 返回 None。
        返回值:
            service 应触发 pytest.skip。
        """
        client = FakeDBClient(None)

        with pytest.raises(pytest.skip.Exception):
            FactorMiningDBService.first_selected_run_id(client)
