from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sshtunnel import SSHTunnelForwarder


@dataclass(frozen=True)
class DatabaseEndpoint:
    host: str
    port: int


@contextmanager
def open_database_endpoint(settings):
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

    tunnel = SSHTunnelForwarder(**ssh_kwargs)
    try:
        tunnel.start()
        yield DatabaseEndpoint(host="127.0.0.1", port=tunnel.local_bind_port)
    finally:
        tunnel.stop()
