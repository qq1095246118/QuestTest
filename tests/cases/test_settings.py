"""框架环境变量配置加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.settings import SettingsLoader


class TestAuthenticationSettings:
    """验证测试环境账号配置和环境变量覆盖行为。"""

    def test_account_credentials_are_loaded_from_environment_variables(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """设置四个测试账号环境变量，并验证有权限和无权限凭据不会相互混用。"""

        monkeypatch.setenv("AUTOMATION_PRIVILEGED_EMAIL", "privileged@example.test")
        monkeypatch.setenv("AUTOMATION_PRIVILEGED_PASSWORD", "privileged-test-password")
        monkeypatch.setenv("AUTOMATION_RESTRICTED_EMAIL", "restricted@example.test")
        monkeypatch.setenv("AUTOMATION_RESTRICTED_PASSWORD", "restricted-test-password")

        settings = SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )

        assert settings.authentication.privileged.email == "privileged@example.test"
        assert settings.authentication.privileged.password == "privileged-test-password"
        assert settings.authentication.restricted.email == "restricted@example.test"
        assert settings.authentication.restricted.password == "restricted-test-password"

    def test_tracked_test_yaml_contains_configured_test_environment(self) -> None:
        """读取测试环境配置，并验证地址、账号和数据库连接指向已授权的测试环境。"""

        project_root = Path(__file__).resolve().parents[2]
        raw_config = yaml.safe_load((project_root / "config" / "test.yaml").read_text(encoding="utf-8"))

        assert isinstance(raw_config, dict)
        assert raw_config["environment"] == "test"
        assert raw_config["api"]["base_url"] == "https://test-factor-backend.questvector.ai/api/v1"
        assert raw_config["factor_combo"]["agent_base_url"] == (
            "https://test-factor-frontend.questvector.ai/api/v2"
        )
        assert raw_config["factor_data"]["mcp_url"] == (
            "https://test-factor-frontend.questvector.ai/mcp/factor-data"
        )
        assert raw_config["factor_data"]["auth_token"]
        authentication = raw_config["authentication"]
        assert authentication["privileged_email"] == "haoran@gmail.com"
        assert authentication["restricted_email"] == "wuquanxian@qq.com"
        assert authentication["privileged_password"]
        assert authentication["restricted_password"]
        database = raw_config["database"]
        assert database["driver"] == "mysql"
        assert database["host"] == "43.167.190.122"
        assert database["port"] == 3306
        assert database["name"] == "factor_db"
        assert database["username"] == "factor_app"
        assert database["password"]

    def test_real_connection_settings_are_loaded_from_environment_variables(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """设置服务和数据库环境变量，并验证运行时覆盖优先于测试 YAML。"""

        monkeypatch.setenv("AUTOMATION_API_BASE_URL", "https://factor.example.test/api/v1")
        monkeypatch.setenv("AUTOMATION_FACTOR_COMBO_AGENT_BASE_URL", "https://agent.example.test/api/v2")
        monkeypatch.setenv("AUTOMATION_FACTOR_DATA_MCP_URL", "https://mcp.example.test/mcp/factor-data")
        monkeypatch.setenv("AUTOMATION_FACTOR_DATA_MCP_TOKEN", "factor-data-test-token")
        monkeypatch.setenv("AUTOMATION_DB_HOST", "db.example.test")
        monkeypatch.setenv("AUTOMATION_DB_NAME", "factor_test")
        monkeypatch.setenv("AUTOMATION_DB_USERNAME", "factor_user")
        monkeypatch.setenv("AUTOMATION_DB_PASSWORD", "database-test-password")

        settings = SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )

        assert settings.api.base_url == "https://factor.example.test/api/v1"
        assert settings.factor_combo.agent_base_url == "https://agent.example.test/api/v2"
        assert settings.factor_data.mcp_url == "https://mcp.example.test/mcp/factor-data"
        mcp_token_matches = settings.factor_data.auth_token == "factor-data-test-token"
        assert mcp_token_matches
        assert "factor-data-test-token" not in repr(settings.factor_data)
        assert settings.database.host == "db.example.test"
        assert settings.database.name == "factor_test"
        assert settings.database.username == "factor_user"
        database_password_matches = settings.database.password == "database-test-password"
        assert database_password_matches
        assert "database-test-password" not in repr(settings.database)

    def test_test_host_allowlists_are_loaded_and_can_be_overridden(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """测试服务和数据库主机白名单应支持 YAML 与运行时覆盖。"""

        monkeypatch.setenv(
            "AUTOMATION_TEST_ALLOWED_HOSTS",
            "mcp.example.test, api.example.test, mcp.example.test",
        )
        monkeypatch.setenv(
            "AUTOMATION_TEST_ALLOWED_DATABASE_HOSTS",
            "db.example.test",
        )

        settings = SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )

        assert settings.environment_safety.allowed_hosts == (
            "mcp.example.test",
            "api.example.test",
        )
        assert settings.environment_safety.allowed_database_hosts == ("db.example.test",)

    def test_test_host_allowlists_reject_url_and_wildcard_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """allowlist 必须是裸主机名，不能用 URL 或通配符扩大边界。"""

        monkeypatch.setenv(
            "AUTOMATION_TEST_ALLOWED_HOSTS",
            "https://api.example.test/*",
        )

        with pytest.raises(ValueError, match="bare host names"):
            SettingsLoader.load(
                environment="test",
                project_root=Path(__file__).resolve().parents[2],
            )


class TestFactorComboSettings:
    """验证组合因子真实流程的默认运行边界。"""

    def test_cleanup_is_enabled_by_default_for_test_isolation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未设置环境变量时默认开启测试数据清理。"""

        monkeypatch.delenv("AUTOMATION_FACTOR_COMBO_CLEANUP_TEST_DATA", raising=False)
        settings = SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )

        assert settings.factor_combo.cleanup_test_data is True

    def test_default_research_round_limit_matches_current_e2e_flow(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未设置环境变量时使用新版流程规定的两轮研究上限。"""

        monkeypatch.delenv("AUTOMATION_FACTOR_COMBO_MAX_RESEARCH_ROUNDS", raising=False)
        settings = SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )

        assert settings.factor_combo.max_research_rounds == 2
