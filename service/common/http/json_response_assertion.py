from __future__ import annotations

import json
from typing import Any

import allure
import pytest


class JSONResponseAssertionService:
    """JSON 响应断言和 Allure 附件服务。

    请求参数:
        不需要实例化，直接通过静态方法校验接口响应体并输出原始 JSON 附件。
    返回值:
        提供成功信封、错误响应和 JSON 附件能力。
    """

    @staticmethod
    def success_errors(body: Any) -> list[str]:
        """检查通用成功响应信封。

        请求参数:
            body: 接口响应 JSON 解析结果。
        返回值:
            错误信息列表；空列表表示 success=True 且包含 data。
        """
        errors = []
        if not isinstance(body, dict):
            return ["body must be dict"]
        if body.get("success") is not True:
            errors.append("success must be True")
        if "data" not in body:
            errors.append("body must contain data")
        return errors

    @staticmethod
    def attach_json(name: str, body: Any) -> str:
        """把原始 JSON 附加到 Allure 并返回格式化文本。

        请求参数:
            name: Allure 附件名称。
            body: 需要输出的 JSON 可序列化对象。
        返回值:
            格式化后的 JSON 字符串。
        """
        text = json.dumps(body, ensure_ascii=False, indent=2, default=str)
        allure.attach(text, name=name, attachment_type=allure.attachment_type.JSON)
        return text

    @staticmethod
    def fail_with_api_json(body: Any, name: str = "接口返回 JSON") -> None:
        """用接口原始 JSON 作为失败信息终止用例。

        请求参数:
            body: 接口响应 JSON 解析结果。
            name: Allure 附件名称。
        返回值:
            无；调用后 pytest.fail。
        """
        text = JSONResponseAssertionService.attach_json(name, body)
        pytest.fail(f"{name}:\n{text}")

    @staticmethod
    def fail_with_two_json(first_name: str, first_body: Any, second_name: str, second_body: Any) -> None:
        """用两个原始 JSON 作为失败信息终止用例。

        请求参数:
            first_name: 第一个 JSON 的附件名称。
            first_body: 第一个 JSON 对象。
            second_name: 第二个 JSON 的附件名称。
            second_body: 第二个 JSON 对象。
        返回值:
            无；调用后 pytest.fail。
        """
        first_text = JSONResponseAssertionService.attach_json(first_name, first_body)
        second_text = JSONResponseAssertionService.attach_json(second_name, second_body)
        pytest.fail(f"{first_name}:\n{first_text}\n\n{second_name}:\n{second_text}")
