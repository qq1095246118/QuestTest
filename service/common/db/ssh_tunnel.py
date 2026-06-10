from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sshtunnel import SSHTunnelForwarder


@dataclass(frozen=True)
class DatabaseEndpoint:
    """数据库可访问地址描述。

    请求参数:
        host: 当前环境可访问的数据库 host。
        port: 当前环境可访问的数据库 port。
    返回值:
        不可变的数据对象，供 DB client 创建连接时使用。
    """

    host: str
    port: int


class DatabaseEndpointService:
    """数据库 endpoint 解析服务。

    请求参数:
        不需要实例化，直接通过静态方法解析直连或 SSH tunnel 后的数据库地址。
    返回值:
        提供数据库 endpoint 上下文管理能力的 service 类。
    """

    @staticmethod
    @contextmanager
    def open_database_endpoint(settings):
        """根据配置返回可访问的数据库 endpoint。

        请求参数:
            settings: 环境配置对象，包含 factor_db_* 和可选 factor_ssh_* 字段。
        返回值:
            上下文管理器，yield DatabaseEndpoint；未开启 SSH 时返回直连地址，开启 SSH 时返回本地 tunnel 地址。
        """
        db_endpoint = DatabaseEndpoint(host=settings.factor_db_host, port=settings.factor_db_port)
        if not settings.factor_ssh_enabled:
            yield db_endpoint
            return

        ssh_kwargs = {
            "ssh_address_or_host": (settings.factor_ssh_host, settings.factor_ssh_port),
            "remote_bind_address": (settings.factor_db_host, settings.factor_db_port),
        }
        if settings.factor_ssh_user:
            ssh_kwargs["ssh_username"] = settings.factor_ssh_user
        if settings.factor_ssh_key_path:
            ssh_kwargs["ssh_pkey"] = str(Path(settings.factor_ssh_key_path).expanduser())
        if settings.factor_ssh_password:
            ssh_kwargs["ssh_password"] = settings.factor_ssh_password
            ssh_kwargs["ssh_private_key_password"] = settings.factor_ssh_password

        tunnel = SSHTunnelForwarder(**ssh_kwargs)
        try:
            tunnel.start()
            yield DatabaseEndpoint(host="127.0.0.1", port=tunnel.local_bind_port)
        finally:
            tunnel.stop()
