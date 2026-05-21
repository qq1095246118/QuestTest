"""数据库基础设施模块。

本模块封装 MySQL 连接、查询和写入能力，供服务层和测试入口复用。
"""

import pymysql
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

class DBClient:
    """
    底层 MySQL 数据库连接客户端，用于支持 DQC 的数据对比校验。
    """
    def __init__(self):
        try:
            self.conn = pymysql.connect(
                host=settings.db_host,
                port=settings.db_port,
                user=settings.db_user,
                password=settings.db_password,
                database=settings.db_name,
                cursorclass=pymysql.cursors.DictCursor
            )
            logger.info(f"Successfully connected to database {settings.db_name} at {settings.db_host}")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise e

    def query(self, sql: str, params: tuple = None):
        """执行查询并返回结果（字典列表格式）"""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, params)
                result = cursor.fetchall()
                return result
        except Exception as e:
            logger.error(f"Query execution failed: {sql} | Error: {e}")
            raise e

    def execute(self, sql: str, params: tuple = None):
        """执行增删改操作并返回受影响的行数"""
        try:
            with self.conn.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
                self.conn.commit()
                return affected_rows
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Execute failed: {sql} | Error: {e}")
            raise e

    def execute_many(self, sql: str, params_list: list):
        """批量执行增删改操作并返回受影响的行数"""
        try:
            with self.conn.cursor() as cursor:
                affected_rows = cursor.executemany(sql, params_list)
                self.conn.commit()
                return affected_rows
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Execute many failed: {sql} | Error: {e}")
            raise e

    def close(self):
        """关闭数据库连接"""
        if self.conn and self.conn.open:
            self.conn.close()
            logger.info("Database connection closed.")
