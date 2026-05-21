"""数据库 DAO 基础模块。

本模块放置通用数据访问封装，避免业务层直接重复拼接基础查询逻辑。
"""

import logging
from typing import List, Dict, Any, Optional
from infrastructure.database.db_client import DBClient

logger = logging.getLogger(__name__)

class BaseDAO:
    """
    基础数据访问对象 (DAO)，提供通用的单表增删改查 (CRUD) 底层调用代码。
    子类只需继承并定义 `table_name` 和 `primary_key` 即可。
    """
    def __init__(self, db_client: DBClient):
        self.db = db_client
        self.table_name = ""
        self.primary_key = "id"

    def get_by_id(self, record_id: Any) -> Optional[Dict[str, Any]]:
        """根据主键查询单条记录"""
        sql = f"SELECT * FROM {self.table_name} WHERE {self.primary_key} = %s LIMIT 1"
        result = self.db.query(sql, (record_id,))
        return result[0] if result else None

    def get_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查询所有记录，默认限制 100 条防止内存溢出"""
        sql = f"SELECT * FROM {self.table_name} LIMIT %s"
        return self.db.query(sql, (limit,))

    def insert(self, data: Dict[str, Any]) -> int:
        """插入单条记录"""
        keys = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO {self.table_name} ({keys}) VALUES ({placeholders})"
        return self.db.execute(sql, tuple(data.values()))

    def insert_many(self, data_list: List[Dict[str, Any]]) -> int:
        """批量插入多条记录"""
        if not data_list:
            return 0
        keys = ", ".join(data_list[0].keys())
        placeholders = ", ".join(["%s"] * len(data_list[0]))
        sql = f"INSERT INTO {self.table_name} ({keys}) VALUES ({placeholders})"
        params_list = [tuple(item.values()) for item in data_list]
        return self.db.execute_many(sql, params_list)

    def update_by_id(self, record_id: Any, update_data: Dict[str, Any]) -> int:
        """根据主键更新单条记录"""
        if not update_data:
            return 0
        set_clause = ", ".join([f"{k} = %s" for k in update_data.keys()])
        sql = f"UPDATE {self.table_name} SET {set_clause} WHERE {self.primary_key} = %s"
        params = tuple(update_data.values()) + (record_id,)
        return self.db.execute(sql, params)

    def delete_by_id(self, record_id: Any) -> int:
        """根据主键删除单条记录"""
        sql = f"DELETE FROM {self.table_name} WHERE {self.primary_key} = %s"
        return self.db.execute(sql, (record_id,))


class BinanceKlineDAO(BaseDAO):
    """
    Binance U本位永续 1小时 K线表 (binance_1h_usdm_kline_raw)
    包含: symbol, timestamp, interval, open, high, low, close, volume 等字段
    """
    def __init__(self, db_client: DBClient):
        super().__init__(db_client)
        self.table_name = "binance_1h_usdm_kline_raw"
        self.primary_key = "id" # 根据实际表结构调整主键名称

    def get_klines_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """按交易对查询 K 线数据"""
        sql = f"SELECT * FROM {self.table_name} WHERE symbol = %s ORDER BY timestamp DESC LIMIT %s"
        return self.db.query(sql, (symbol, limit))


class CoinglassOpenInterestDAO(BaseDAO):
    """
    Coinglass 未平仓合约数据表 (coinglass_open_interest_raw)
    包含: symbol, timestamp, open_interest, exchange 等字段
    """
    def __init__(self, db_client: DBClient):
        super().__init__(db_client)
        self.table_name = "coinglass_open_interest_raw"
        self.primary_key = "id"

    def get_oi_by_symbol_exchange(self, symbol: str, exchange: str, limit: int = 100) -> List[Dict[str, Any]]:
        """按交易对和交易所查询 OI 数据"""
        sql = f"SELECT * FROM {self.table_name} WHERE symbol = %s AND exchange = %s ORDER BY timestamp DESC LIMIT %s"
        return self.db.query(sql, (symbol, exchange, limit))


class DQCIssuesDAO(BaseDAO):
    """
    数据质量告警记录表 (dqc_issues)
    包含: issue_id, table_name, issue_type, issue_detail, status, created_at
    """
    def __init__(self, db_client: DBClient):
        super().__init__(db_client)
        self.table_name = "dqc_issues"
        self.primary_key = "issue_id" # 假设主键为 issue_id

    def get_unresolved_issues(self, table_name: str = None) -> List[Dict[str, Any]]:
        """查询未解决的 DQC 异常"""
        sql = f"SELECT * FROM {self.table_name} WHERE status = 'UNRESOLVED'"
        params = []
        if table_name:
            sql += " AND table_name = %s"
            params.append(table_name)
        sql += " ORDER BY created_at DESC"
        return self.db.query(sql, tuple(params) if params else None)
