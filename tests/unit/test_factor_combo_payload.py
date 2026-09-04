"""组合因子表单请求构造的离线回归测试。"""

from __future__ import annotations

from typing import Any

import pytest

from service.factor_combo_service import FactorComboService
from tests.resource_scope import TestResourceScope as ResourceScope


pytestmark = pytest.mark.unit


class _StubResponse:
    """提供表单资源跟踪测试需要的最小响应对象。"""

    def __init__(self, payload: object) -> None:
        """保存 ``payload``，供 ``json`` 返回；不执行序列化。"""

        self._payload = payload

    def json(self) -> object:
        """返回构造时保存的响应对象。"""

        return self._payload


class _StubFactorComboAPI:
    """返回固定表单响应的最小 API 替身。"""

    def __init__(self, response: _StubResponse) -> None:
        """保存 ``response``，供表单提交方法返回。"""

        self._response = response

    def submit_form(self, payload: dict[str, Any]) -> _StubResponse:
        """接收任意表单请求并返回固定响应。"""

        return self._response


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
    """验证表单请求构造中的默认配置和因子类型标识。"""

    def test_omitted_method_groups_uses_default_configuration(self) -> None:
        """调用方省略 method_groups 时生成默认规则方法配置。"""

        payload = _service_for_payload().build_form_payload(1, ["factor-a", "factor-b"])

        assert payload["is_sub_factor"] == 1
        assert payload["method_groups"] == {"rule_methods": ["ic_weight"]}
        assert set(payload["configuration_parameters"]) == {
            "objectives",
            "rolling_window",
            "correlation_penalty",
            "transaction_cost",
            "optimize_subfactor_params",
        }

    def test_parent_factor_payload_uses_zero_type_flag(self) -> None:
        """构造母因子表单时使用接口规定的 0 类型标识。"""

        payload = _service_for_payload().build_form_payload(1, ["factor-a"], is_sub_factor=0)

        assert payload["is_sub_factor"] == 0

    def test_invalid_factor_type_flag_is_rejected_before_request(self) -> None:
        """构造不支持的因子类型标识时直接失败，避免发送无效请求。"""

        try:
            _service_for_payload().build_form_payload(1, ["factor-a"], is_sub_factor=2)
        except ValueError as error:
            assert str(error) == "is_sub_factor must be 0 (parent factor) or 1 (sub-factor)"
        else:
            raise AssertionError("invalid is_sub_factor value should fail")

    def test_unexpected_successful_form_response_is_tracked_for_cleanup(self) -> None:
        """负向场景意外收到成功响应时，也登记服务端创建的表单供 Fixture 清理。"""

        scope = ResourceScope()
        scope.track_session(11)
        response = _StubResponse({"success": True, "data": {"form_id": 22}})
        service = FactorComboService(
            chat_api=None,  # type: ignore[arg-type]
            factor_combo_api=_StubFactorComboAPI(response),  # type: ignore[arg-type]
            repository=None,  # type: ignore[arg-type]
            settings=None,  # type: ignore[arg-type]
            scope=scope,
        )

        actual_response = service.submit_form({"session_id": 11})

        assert actual_response is response
        assert scope.cleanable_resource_graph() == {11: {22}}
