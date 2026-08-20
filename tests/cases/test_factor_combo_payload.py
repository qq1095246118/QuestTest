"""组合因子表单请求构造的离线回归测试。"""

from __future__ import annotations

from typing import Any

from service.factor_combo_service import FactorComboService, TestResourceScope as ResourceScope


def _service_for_payload() -> FactorComboService:
    """创建只用于测试请求构造的 Service。

    返回不执行网络请求或数据库访问的 ``FactorComboService``；构造方法本身只依赖输入参数。
    """

    return FactorComboService(
        chat_api=None,  # type: ignore[arg-type]
        factor_combo_api=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        scope=ResourceScope(),
    )


class TestFactorComboPayload:
    """验证表单构造方法区分省略值和显式 JSON null。"""

    def test_omitted_method_groups_uses_default_configuration(self) -> None:
        """调用方省略 method_groups 时生成默认规则方法配置。"""

        payload = _service_for_payload().build_form_payload(1, ["factor-a", "factor-b"])

        assert payload["method_groups"] == {"rule_methods": ["ic_weight"]}

    def test_explicit_null_method_groups_is_preserved_as_json_null(self) -> None:
        """调用方显式传入 None 时保留 JSON null，不被替换成默认配置。"""

        payload = _service_for_payload().build_form_payload(
            1,
            ["factor-a", "factor-b"],
            method_groups=None,
        )

        assert "method_groups" in payload
        assert payload["method_groups"] is None

    def test_explicit_json_values_are_not_rewritten(self) -> None:
        """调用方传入的其他合法 JSON 值按原值保留。"""

        values: list[Any] = [
            {},
            ["ridge", "lasso"],
            "custom-method-config",
            123,
            True,
        ]

        for value in values:
            payload = _service_for_payload().build_form_payload(
                1,
                ["factor-a", "factor-b"],
                method_groups=value,
            )

            assert payload["method_groups"] == value
