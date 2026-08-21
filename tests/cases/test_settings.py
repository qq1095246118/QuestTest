"""框架环境变量配置加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import SettingsLoader


class TestAuthenticationSettings:
    """验证两类账号只通过环境变量注入类型化配置。"""

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
