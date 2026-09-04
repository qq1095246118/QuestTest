"""环境配置加载与类型化访问。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class ApiSettings:
    """保存 HTTP 客户端需要的配置。

    参数来自 YAML 配置和环境变量；实例字段包含接口地址、超时、重试和可选鉴权 Token。
    返回值由 ``SettingsLoader.load`` 创建，供 API 层构造 HTTP 客户端使用。
    """

    base_url: str
    timeout_seconds: float
    retry_attempts: int
    retry_backoff_seconds: float
    auth_token: str | None


@dataclass(frozen=True)
class AccountCredentials:
    """保存一个自动化测试账号的登录凭据。

    参数 ``email`` 和 ``password`` 可来自测试环境 YAML 配置或运行时环境变量；生产凭据不得进入静态配置。
    返回值由 ``SettingsLoader.load`` 创建，供测试启动阶段调用登录接口；任一字段未配置时对应账号不可用。
    """

    email: str | None
    password: str | None


@dataclass(frozen=True)
class AuthenticationSettings:
    """保存有权限和无权限两类测试账号。

    参数 ``privileged`` 用于正常业务请求，``restricted`` 用于验证已登录但权限不足的 403 场景，``non_owner`` 用于
    验证拥有同等业务权限但不拥有目标资源的 404 所有权隔离场景。
    返回值由 ``SettingsLoader.load`` 创建；生产环境账号密码不得写入静态配置。
    """

    privileged: AccountCredentials
    restricted: AccountCredentials
    non_owner: AccountCredentials = field(default_factory=lambda: AccountCredentials(None, None))


@dataclass(frozen=True)
class DatabaseSettings:
    """保存关系型数据库连接所需的配置。

    参数来自 YAML 配置和环境变量；支持 SQLite 的 ``dsn`` 以及 MySQL 的 host、port、name、username、password。
    返回值由 ``SettingsLoader.load`` 创建，供 DB 层按驱动建立连接使用。
    """

    driver: str
    host: str
    port: int
    name: str
    username: str
    password: str | None
    dsn: str | None


@dataclass(frozen=True)
class ReportSettings:
    """保存测试报告输出配置。

    参数为 JUnit XML 的相对或绝对输出路径。
    返回值由 ``SettingsLoader.load`` 创建，供脚本或 CI 读取。
    """

    junit_path: str


@dataclass(frozen=True)
class FactorComboSettings:
    """保存组合因子真实流程测试需要的运行参数。

    参数来自 YAML 配置和环境变量；包含投研 Agent 地址、轮询间隔、轮询超时、最大研究轮次及 Worker 回调开关。
    返回值由 ``SettingsLoader.load`` 创建，供组合因子测试 Fixture 使用。
    """

    agent_uid: str | None
    poll_interval_seconds: float
    poll_timeout_seconds: float
    max_research_rounds: int
    worker_contracts_enabled: bool
    cleanup_test_data: bool
    agent_base_url: str | None
    refresh_poll_interval_seconds: float
    refresh_poll_timeout_seconds: float
    max_refresh_polls: int
    max_technical_retries: int


@dataclass(frozen=True)
class Settings:
    """聚合当前测试环境的全部类型化配置。

    参数包括环境名称以及 API、认证账号、数据库、组合因子、报告子配置。
    返回值由 ``SettingsLoader.load`` 返回，供 Fixture、API、DB 和 Service 使用。
    """

    environment: str
    api: ApiSettings
    authentication: AuthenticationSettings
    database: DatabaseSettings
    factor_combo: FactorComboSettings
    reports: ReportSettings


class SettingsLoader:
    """按默认配置、环境配置、环境变量的优先级加载框架配置。"""

    _ENVIRONMENT_OVERRIDES: dict[str, tuple[str, str]] = {
        "AUTOMATION_API_BASE_URL": ("api", "base_url"),
        "AUTOMATION_API_AUTH_TOKEN": ("api", "auth_token"),
        "AUTOMATION_API_TIMEOUT_SECONDS": ("api", "timeout_seconds"),
        "AUTOMATION_API_RETRY_ATTEMPTS": ("api", "retry_attempts"),
        "AUTOMATION_API_RETRY_BACKOFF_SECONDS": ("api", "retry_backoff_seconds"),
        "AUTOMATION_PRIVILEGED_EMAIL": ("authentication", "privileged_email"),
        "AUTOMATION_PRIVILEGED_PASSWORD": ("authentication", "privileged_password"),
        "AUTOMATION_RESTRICTED_EMAIL": ("authentication", "restricted_email"),
        "AUTOMATION_RESTRICTED_PASSWORD": ("authentication", "restricted_password"),
        "AUTOMATION_NON_OWNER_EMAIL": ("authentication", "non_owner_email"),
        "AUTOMATION_NON_OWNER_PASSWORD": ("authentication", "non_owner_password"),
        "AUTOMATION_DB_DRIVER": ("database", "driver"),
        "AUTOMATION_DB_HOST": ("database", "host"),
        "AUTOMATION_DB_PORT": ("database", "port"),
        "AUTOMATION_DB_NAME": ("database", "name"),
        "AUTOMATION_DB_USERNAME": ("database", "username"),
        "AUTOMATION_DB_PASSWORD": ("database", "password"),
        "AUTOMATION_DB_DSN": ("database", "dsn"),
        "AUTOMATION_FACTOR_COMBO_AGENT_UID": ("factor_combo", "agent_uid"),
        "AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL": ("factor_combo", "agent_base_url"),
        "AUTOMATION_FACTOR_COMBO_POLL_INTERVAL_SECONDS": ("factor_combo", "poll_interval_seconds"),
        "AUTOMATION_FACTOR_COMBO_POLL_TIMEOUT_SECONDS": ("factor_combo", "poll_timeout_seconds"),
        "AUTOMATION_FACTOR_COMBO_MAX_RESEARCH_ROUNDS": ("factor_combo", "max_research_rounds"),
        "AUTOMATION_FACTOR_COMBO_WORKER_CONTRACTS": ("factor_combo", "worker_contracts_enabled"),
        "AUTOMATION_FACTOR_COMBO_CLEANUP_TEST_DATA": ("factor_combo", "cleanup_test_data"),
        "AUTOMATION_FACTOR_COMBO_REFRESH_POLL_INTERVAL_SECONDS": (
            "factor_combo",
            "refresh_poll_interval_seconds",
        ),
        "AUTOMATION_FACTOR_COMBO_REFRESH_POLL_TIMEOUT_SECONDS": (
            "factor_combo",
            "refresh_poll_timeout_seconds",
        ),
        "AUTOMATION_FACTOR_COMBO_MAX_REFRESH_POLLS": ("factor_combo", "max_refresh_polls"),
        "AUTOMATION_FACTOR_COMBO_MAX_TECHNICAL_RETRIES": ("factor_combo", "max_technical_retries"),
    }

    @classmethod
    def load(cls, environment: str | None = None, project_root: Path | None = None) -> Settings:
        """加载指定环境的配置。

        参数 ``environment`` 是 ``config/<environment>.yaml`` 的环境名，缺省时读取 ``AUTOMATION_ENV`` 或使用 ``test``；
        ``project_root`` 是项目根目录，缺省时从当前模块推导。
        返回合并默认配置、环境配置和环境变量后的 ``Settings``；配置文件缺失或字段类型错误时抛出异常。
        """

        root = project_root or Path(__file__).resolve().parents[1]
        selected_environment = environment or os.getenv("AUTOMATION_ENV", "test")
        default_data = cls._read_yaml(root / "config" / "default.yaml")
        environment_data = cls._read_yaml(root / "config" / f"{selected_environment}.yaml")
        merged_data = cls._deep_merge(default_data, environment_data)
        cls._apply_environment_overrides(merged_data)
        merged_data["environment"] = selected_environment
        return cls._to_settings(merged_data)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        """读取一个 YAML 配置文件。

        参数 ``path`` 是需要读取的 YAML 文件路径。
        返回解析后的字典；文件不存在、根节点不是对象或 YAML 格式错误时抛出异常。
        """

        with path.open("r", encoding="utf-8") as file:
            content = yaml.safe_load(file) or {}
        if not isinstance(content, dict):
            raise ValueError(f"Configuration root must be an object: {path}")
        return content

    @classmethod
    def _deep_merge(cls, base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
        """递归合并两层配置字典。

        参数 ``base`` 是低优先级默认配置，``override`` 是高优先级环境配置。
        返回不修改输入对象的合并结果；同名非对象字段由 ``override`` 覆盖。
        """

        result = dict(base)
        for key, value in override.items():
            base_value = result.get(key)
            if isinstance(base_value, Mapping) and isinstance(value, Mapping):
                result[key] = cls._deep_merge(base_value, value)
            else:
                result[key] = value
        return result

    @classmethod
    def _apply_environment_overrides(cls, config: dict[str, Any]) -> None:
        """把已设置的环境变量覆盖到嵌套配置中。

        参数 ``config`` 是待修改的合并配置字典。
        不返回值；仅处理 ``_ENVIRONMENT_OVERRIDES`` 中明确定义的变量，未设置的变量不改变配置。
        """

        for variable, (section, key) in cls._ENVIRONMENT_OVERRIDES.items():
            value = os.getenv(variable)
            if value is not None:
                config.setdefault(section, {})[key] = value

    @staticmethod
    def _to_settings(data: Mapping[str, Any]) -> Settings:
        """将原始配置字典转换为类型化配置对象。

        参数 ``data`` 是已完成合并的原始配置字典。
        返回 ``Settings``；缺少必需的结构字段或数值无法转换时抛出 ``ValueError``。
        """

        api = SettingsLoader._section(data, "api")
        authentication = SettingsLoader._section(data, "authentication")
        database = SettingsLoader._section(data, "database")
        factor_combo = SettingsLoader._section(data, "factor_combo")
        reports = SettingsLoader._section(data, "reports")
        return Settings(
            environment=str(data.get("environment", "test")),
            api=ApiSettings(
                base_url=str(api.get("base_url", "")).rstrip("/"),
                timeout_seconds=float(api.get("timeout_seconds", 60)),
                retry_attempts=int(api.get("retry_attempts", 0)),
                retry_backoff_seconds=float(api.get("retry_backoff_seconds", 0)),
                auth_token=SettingsLoader._normalize_auth_token(api.get("auth_token")),
            ),
            authentication=AuthenticationSettings(
                privileged=AccountCredentials(
                    email=SettingsLoader._optional_string(authentication.get("privileged_email")),
                    password=SettingsLoader._optional_string(authentication.get("privileged_password")),
                ),
                restricted=AccountCredentials(
                    email=SettingsLoader._optional_string(authentication.get("restricted_email")),
                    password=SettingsLoader._optional_string(authentication.get("restricted_password")),
                ),
                non_owner=AccountCredentials(
                    email=SettingsLoader._optional_string(authentication.get("non_owner_email")),
                    password=SettingsLoader._optional_string(authentication.get("non_owner_password")),
                ),
            ),
            database=DatabaseSettings(
                driver=str(database.get("driver", "sqlite")).lower(),
                host=str(database.get("host", "")),
                port=int(database.get("port", 0) or 0),
                name=str(database.get("name", "")),
                username=str(database.get("username", "")),
                password=SettingsLoader._optional_string(database.get("password")),
                dsn=SettingsLoader._optional_string(database.get("dsn")),
            ),
            factor_combo=FactorComboSettings(
                agent_uid=SettingsLoader._optional_string(factor_combo.get("agent_uid")),
                poll_interval_seconds=float(factor_combo.get("poll_interval_seconds", 5)),
                poll_timeout_seconds=float(factor_combo.get("poll_timeout_seconds", 600)),
                max_research_rounds=int(factor_combo.get("max_research_rounds", 2)),
                worker_contracts_enabled=SettingsLoader._to_boolean(
                    factor_combo.get("worker_contracts_enabled", False)
                ),
                cleanup_test_data=SettingsLoader._to_boolean(factor_combo.get("cleanup_test_data", True)),
                agent_base_url=SettingsLoader._optional_string(factor_combo.get("agent_base_url")),
                refresh_poll_interval_seconds=float(factor_combo.get("refresh_poll_interval_seconds", 10)),
                refresh_poll_timeout_seconds=float(factor_combo.get("refresh_poll_timeout_seconds", 10800)),
                max_refresh_polls=int(factor_combo.get("max_refresh_polls", 1080)),
                max_technical_retries=int(factor_combo.get("max_technical_retries", 2)),
            ),
            reports=ReportSettings(junit_path=str(reports.get("junit_path", "reports/junit.xml"))),
        )

    @staticmethod
    def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        """取得并校验一个配置区段。

        参数 ``data`` 是原始配置字典，``name`` 是区段名称。
        返回该区段的映射；区段不存在或不是对象时抛出 ``ValueError``。
        """

        section = data.get(name)
        if not isinstance(section, Mapping):
            raise ValueError(f"Configuration section must be an object: {name}")
        return section

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        """将可选配置值标准化为字符串或 ``None``。

        参数 ``value`` 是原始配置值。
        返回去除首尾空白后的字符串；空值和空白字符串返回 ``None``。
        """

        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_auth_token(value: Any) -> str | None:
        """标准化 API Token 并移除可选的 Bearer 前缀。

        参数 ``value`` 是 YAML 或环境变量中的 Token 值。
        返回不含 ``Bearer`` 前缀的 Token；空值返回 ``None``，避免 HTTP 客户端生成重复鉴权前缀。
        """

        normalized = SettingsLoader._optional_string(value)
        if normalized is None:
            return None
        if normalized.lower().startswith("bearer "):
            normalized = normalized[7:].strip()
        return normalized or None

    @staticmethod
    def _to_boolean(value: Any) -> bool:
        """将 YAML 或环境变量中的布尔配置转换为布尔值。

        参数 ``value`` 是原始配置值，可为布尔值、数字或字符串。
        返回转换后的布尔值；无法识别的值抛出 ``ValueError``，避免错误开启真实环境写入行为。
        """

        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off", "none", "null"}:
            return False
        raise ValueError(f"Configuration value must be boolean-like: {value!r}")
