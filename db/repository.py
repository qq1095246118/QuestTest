"""Repository 层示例：用语义化方法封装实体的数据访问。"""

from __future__ import annotations

from dataclasses import dataclass

from db.client import DatabaseClient


@dataclass(frozen=True)
class SampleRecord:
    """表示框架示例使用的一条测试记录。

    参数 ``record_id`` 是主键，``name`` 是记录名称，``created_at`` 是数据库生成的创建时间。
    返回值由 ``SampleRecordRepository`` 的创建和查询方法产生。
    """

    record_id: int
    name: str
    created_at: str


class SampleRecordRepository:
    """演示 Repository 如何集中保存一个实体的 SQL 和数据转换。"""

    def __init__(self, client: DatabaseClient) -> None:
        """初始化示例仓储。

        参数 ``client`` 是提供参数化查询和事务的 ``DatabaseClient``。
        不返回值；该仓储只操作 ``sample_records`` 示例表。
        """

        self._client = client

    def initialize_schema(self) -> None:
        """创建 SQLite 示例表。

        不接收参数。
        不返回值；表已存在时保持不变，用于本地 Mock/SQLite 示例测试，不作为真实业务表建模方式。
        """

        self._client.execute(
            """
            CREATE TABLE IF NOT EXISTS sample_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def create(self, name: str) -> SampleRecord:
        """创建一条示例记录并返回其持久化结果。

        参数 ``name`` 是要保存的非空名称。
        返回新建的 ``SampleRecord``；插入失败或读取不到新记录时抛出异常。
        """

        result = self._client.execute("INSERT INTO sample_records (name) VALUES (?)", (name,))
        if result.lastrowid is None:
            raise RuntimeError("Database did not return the inserted sample record ID")
        record = self.find_by_id(int(result.lastrowid))
        if record is None:
            raise RuntimeError("Inserted sample record could not be read back")
        return record

    def find_by_id(self, record_id: int) -> SampleRecord | None:
        """按主键查询一条示例记录。

        参数 ``record_id`` 是待查询记录的主键。
        返回 ``SampleRecord``；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            "SELECT id, name, created_at FROM sample_records WHERE id = ?",
            (record_id,),
        )
        if row is None:
            return None
        return SampleRecord(
            record_id=int(row["id"]),
            name=str(row["name"]),
            created_at=str(row["created_at"]),
        )

    def delete_by_id(self, record_id: int) -> bool:
        """按主键删除一条示例记录。

        参数 ``record_id`` 是待删除记录的主键。
        返回 ``True`` 表示实际删除了一条记录，返回 ``False`` 表示记录不存在。
        """

        result = self._client.execute("DELETE FROM sample_records WHERE id = ?", (record_id,))
        return result.rowcount == 1
