"""Service 与 DB 集成示例，使用临时 SQLite 数据库。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.client import DatabaseClient
from db.repository import SampleRecordRepository
from service.example_service import SampleRecordService
from tools.data_factory import TestDataFactory


class TestSampleRecordService:
    """验证 Service 通过 Repository 完成创建、查询和清理。"""

    @pytest.mark.integration
    def test_register_record_persists_and_can_be_cleaned(self, tmp_path: Path) -> None:
        """验证 Service 创建的数据可被 DB 查询并在结束时清理。

        参数 ``tmp_path`` 是 pytest 提供的隔离临时目录。
        不返回值；持久化结果或清理结果不符合预期时抛出 ``AssertionError``。
        """

        database_path = tmp_path / "sample-records.sqlite3"
        client = DatabaseClient(lambda: self._connect_sqlite(database_path))
        repository = SampleRecordRepository(client)
        repository.initialize_schema()
        service = SampleRecordService(repository)
        name = TestDataFactory.unique_name("framework-sample")

        record = service.register_record(name)

        persisted_record = repository.find_by_id(record.record_id)
        try:
            assert persisted_record == record
        finally:
            deleted = repository.delete_by_id(record.record_id)
        assert deleted is True
        assert repository.find_by_id(record.record_id) is None

    @staticmethod
    def _connect_sqlite(database_path: Path) -> sqlite3.Connection:
        """建立供单个示例操作使用的 SQLite 文件连接。

        参数 ``database_path`` 是临时 SQLite 文件路径。
        返回新的 SQLite 连接；文件由 pytest 临时目录管理并在测试后删除。
        """

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection
