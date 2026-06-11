from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from service.common.http.response_utils import HTTPResponseService


class FactorListCompareService:
    """因子列表接口数据比较服务。

    请求参数:
        不需要实例化，直接通过静态方法比较接口响应、DB 数据和上下游接口数据。
    返回值:
        提供接口自身校验、接口与 DB 比较、主题上下游关系比较和数据归一化能力。
    """

    BASIC_FACTOR_FIELDS = (
        "id",
        "serial_number",
        "serial_prefix",
        "factor_name",
        "cn_name",
        "factor_tags",
        "level",
        "max_level",
        "child_factor_count",
        "created_by",
        "created_by_uid",
        "operator_by",
        "operator_by_uid",
    )

    TIME_FACTOR_FIELDS = ("created_at", "updated_at")

    DETAIL_FACTOR_FIELDS = (
        "id",
        "factor_id",
        "serial_number",
        "name",
        "update_interval",
        "hit_count",
        "strategy_status",
        "status",
    )

    THEME_FACTOR_FIELDS = ("id", "theme_key", "theme_name", "cn_name", "status")

    @staticmethod
    def success_body_errors(body: Any) -> list[str]:
        """检查通用成功响应信封是否符合约定。

        请求参数:
            body: 接口响应 JSON 解析结果。
        返回值:
            错误信息列表；空列表表示响应包含 success=True 和 data。
        """
        return HTTPResponseService.success_body_errors(body)

    @staticmethod
    def factor_list_shape_errors(body: Any) -> list[str]:
        """检查因子列表响应结构是否满足后续用例读取要求。

        请求参数:
            body: 因子列表接口响应 JSON 解析结果。
        返回值:
            错误信息列表；空列表表示 data、items、pagination 和核心字段结构可用。
        """
        errors = FactorListCompareService.success_body_errors(body)
        if errors:
            return errors

        data = body["data"]
        if not isinstance(data, dict):
            return ["data must be dict"]
        if not isinstance(data.get("items"), list):
            errors.append("data.items must be list")
        if not isinstance(data.get("pagination"), dict):
            errors.append("data.pagination must be dict")
        if errors:
            return errors

        pagination = data["pagination"]
        for field in ("page", "limit", "total"):
            if field not in pagination:
                errors.append(f"pagination.{field} is required")
            elif not isinstance(pagination[field], int):
                errors.append(f"pagination.{field} must be int")

        required_item_fields = ("id", "serial_number", "factor_name", "cn_name", "factor_detail", "themes")
        for index, item in enumerate(data["items"]):
            if not isinstance(item, dict):
                errors.append(f"items[{index}] must be dict")
                continue
            for field in required_item_fields:
                if field not in item:
                    errors.append(f"items[{index}].{field} is required")
            if "themes" in item and not isinstance(item["themes"], list):
                errors.append(f"items[{index}].themes must be list")
            if "factor_detail" in item and not isinstance(item["factor_detail"], dict):
                errors.append(f"items[{index}].factor_detail must be dict")
        return errors

    @staticmethod
    def factor_list_api_errors(
        status_code: int,
        body: Any,
        expected_page: int | None = None,
        expected_limit: int | None = None,
    ) -> list[str]:
        """检查因子列表接口自身的协议、响应信封、分页和字段结构。

        请求参数:
            status_code: 因子列表接口 HTTP 状态码。
            body: 因子列表接口响应 JSON 解析结果。
            expected_page: 期望返回的分页页码；不传时不校验页码回显。
            expected_limit: 期望返回的分页条数；不传时不校验 limit 回显和当前页条数上限。
        返回值:
            错误信息列表；空列表表示接口自身基础响应符合用例要求。
        """
        errors = []
        if status_code != 200:
            errors.append(f"status_code must be 200, got {status_code}")

        errors.extend(FactorListCompareService.factor_list_shape_errors(body))
        if errors:
            return errors

        pagination = body["data"]["pagination"]
        items = body["data"]["items"]

        if expected_page is not None and pagination["page"] != expected_page:
            errors.append(f"pagination.page mismatch: expected={expected_page!r}, actual={pagination['page']!r}")
        if expected_limit is not None:
            if pagination["limit"] != expected_limit:
                errors.append(f"pagination.limit mismatch: expected={expected_limit!r}, actual={pagination['limit']!r}")
            if len(items) > expected_limit:
                errors.append(f"items length must be <= limit: length={len(items)}, limit={expected_limit}")

        total = pagination["total"]
        if total < 0:
            errors.append(f"pagination.total must be >= 0, got {total}")
        total_pages = pagination.get("total_pages")
        if total_pages is not None and (not isinstance(total_pages, int) or total_pages < 0):
            errors.append(f"pagination.total_pages must be non-negative int, got {total_pages!r}")

        for index, item in enumerate(items):
            errors.extend(FactorListCompareService.factor_list_item_api_errors(item, index))
        return errors

    @staticmethod
    def factor_list_item_api_errors(item: dict[str, Any], index: int) -> list[str]:
        """检查因子列表单条数据自身结构和内部字段关系。

        请求参数:
            item: 因子列表接口返回的单条因子数据。
            index: 当前数据在列表中的下标。
        返回值:
            错误信息列表；空列表表示单条数据字段类型和内部关系符合要求。
        """
        errors = []
        if not isinstance(item.get("id"), int):
            errors.append(f"items[{index}].id must be int")
        for field in ("serial_number", "factor_name", "cn_name"):
            if not isinstance(item.get(field), str):
                errors.append(f"items[{index}].{field} must be str")

        factor_detail = item["factor_detail"]
        if factor_detail.get("factor_id") != item.get("id"):
            errors.append(
                f"items[{index}].factor_detail.factor_id must equal items[{index}].id"
            )
        try:
            FactorListCompareService.normalize_bool(factor_detail.get("is_sub_factor_id"))
        except ValueError as exc:
            errors.append(f"items[{index}].factor_detail.is_sub_factor_id invalid: {exc}")

        for theme_index, theme in enumerate(item["themes"]):
            if not isinstance(theme, dict):
                errors.append(f"items[{index}].themes[{theme_index}] must be dict")
                continue
            for field in FactorListCompareService.THEME_FACTOR_FIELDS:
                if field not in theme:
                    errors.append(f"items[{index}].themes[{theme_index}].{field} is required")
        return errors

    @staticmethod
    def factor_list_business_rule_errors(body: Any, query_params: dict[str, Any]) -> list[str]:
        """检查因子列表接口响应是否满足请求参数对应的接口自身业务规则。

        请求参数:
            body: 因子列表接口响应 JSON 解析结果。
            query_params: 当前用例发起因子列表请求时使用的查询参数。
        返回值:
            错误信息列表；空列表表示分页、筛选和排序结果在接口响应自身范围内符合要求。
        """
        errors = FactorListCompareService.factor_list_shape_errors(body)
        if errors:
            return errors

        items = body["data"]["items"]
        factor_theme = query_params.get("factor_theme")
        factor_detail_status = query_params.get("factor_detail_status")
        if factor_detail_status is None:
            factor_detail_status = query_params.get("status")

        if factor_theme is not None:
            for index, item in enumerate(items):
                theme_keys = [theme.get("theme_key") for theme in item["themes"]]
                if factor_theme not in theme_keys:
                    errors.append(
                        f"items[{index}].themes must contain requested factor_theme={factor_theme!r}"
                    )

        if factor_detail_status is not None:
            for index, item in enumerate(items):
                actual_status = item["factor_detail"].get("status")
                if actual_status != factor_detail_status:
                    errors.append(
                        f"items[{index}].factor_detail.status mismatch: "
                        f"expected={factor_detail_status!r}, actual={actual_status!r}"
                    )

        sort_by = query_params.get("sort_by")
        if sort_by in {"id", "created_at", "updated_at", "factor_name", "cn_name"}:
            errors.extend(FactorListCompareService.factor_list_sort_errors(items, sort_by, query_params.get("sort_order")))
        return errors

    @staticmethod
    def factor_list_sort_errors(items: list[dict[str, Any]], sort_by: str, sort_order: Any) -> list[str]:
        """检查因子列表当前响应页是否符合请求中的排序方向。

        请求参数:
            items: 因子列表接口当前页 items。
            sort_by: 请求中的排序字段。
            sort_order: 请求中的排序方向，支持 asc 和 desc。
        返回值:
            错误信息列表；空列表表示当前页在接口响应自身范围内排序正确。
        """
        values = []
        errors = []
        for index, item in enumerate(items):
            value = item.get(sort_by)
            if value is None:
                errors.append(f"items[{index}].{sort_by} is required for sorting check")
                values.append(value)
                continue
            if sort_by in FactorListCompareService.TIME_FACTOR_FIELDS:
                try:
                    value = FactorListCompareService.normalize_utc_second(value)
                except ValueError as exc:
                    errors.append(f"items[{index}].{sort_by} normalize failed: {exc}")
            values.append(value)

        if errors:
            return errors

        reverse = (sort_order or "desc").lower() != "asc"
        try:
            sorted_values = sorted(values, reverse=reverse)
        except TypeError as exc:
            return [f"items cannot be sorted by {sort_by}: {exc}"]

        if values != sorted_values:
            errors.append(f"items must be sorted by {sort_by} {sort_order or 'desc'}")
        return errors

    @staticmethod
    def factor_list_db_mismatches(api_body: Any, db_page: Any) -> list[str]:
        """比较因子列表接口当前页和 DB 查询当前页是否一致。

        请求参数:
            api_body: 因子列表接口响应 JSON 解析结果。
            db_page: service 查询出的 DB 分页结果。
        返回值:
            不一致信息列表；空列表表示分页、顺序、基础字段、详情和主题均一致。
        """
        errors = FactorListCompareService.factor_list_shape_errors(api_body)
        if errors:
            return errors
        if not isinstance(db_page, dict):
            return ["db_page must be dict"]

        api_data = api_body["data"]
        api_pagination = api_data["pagination"]
        db_pagination = db_page.get("pagination", {})
        api_items = api_data["items"]
        db_items = db_page.get("items")

        if not isinstance(db_items, list):
            return ["db_page.items must be list"]

        if api_pagination["total"] != db_pagination.get("total"):
            errors.append(
                f"pagination.total mismatch: api={api_pagination['total']!r}, db={db_pagination.get('total')!r}"
            )
        if len(api_items) != len(db_items):
            errors.append(f"items length mismatch: api={len(api_items)}, db={len(db_items)}")

        api_ids = [item.get("id") for item in api_items]
        db_ids = [item.get("id") for item in db_items]
        if api_ids != db_ids:
            errors.append(f"item id order mismatch: api={api_ids}, db={db_ids}")

        for index, (api_item, db_item) in enumerate(zip(api_items, db_items)):
            errors.extend(FactorListCompareService.basic_field_mismatches(api_item, db_item, index))
            errors.extend(FactorListCompareService.time_field_mismatches(api_item, db_item, index))
            errors.extend(
                FactorListCompareService.factor_detail_mismatches(
                    api_item["factor_detail"],
                    db_item.get("factor_detail"),
                    index,
                )
            )
            errors.extend(FactorListCompareService.theme_mismatches(api_item["themes"], db_item.get("themes"), index))
        return errors

    @staticmethod
    def theme_relation_mismatches(factor_body: Any, themes_body: Any) -> list[str]:
        """比较因子列表中的主题是否都存在于主题列表接口。

        请求参数:
            factor_body: 因子列表接口响应 JSON 解析结果。
            themes_body: 主题列表接口响应 JSON 解析结果。
        返回值:
            缺失主题信息列表；空列表表示因子列表主题都能在主题列表中找到。
        """
        errors = [
            f"factor list: {message}" for message in FactorListCompareService.factor_list_shape_errors(factor_body)
        ]
        errors.extend(f"theme list: {message}" for message in FactorListCompareService.success_body_errors(themes_body))
        if errors:
            return errors

        themes, theme_errors = FactorListCompareService.extract_theme_items(themes_body["data"])
        if theme_errors:
            return [f"theme list: {message}" for message in theme_errors]

        theme_ids = {theme.get("id") for theme in themes}
        for index, item in enumerate(factor_body["data"]["items"]):
            factor_id = item.get("id")
            for theme in item["themes"]:
                theme_id = theme.get("id")
                if theme_id not in theme_ids:
                    errors.append(f"items[{index}] factor_id={factor_id} missing theme_id={theme_id}")
        return errors

    @staticmethod
    def first_theme_key_from_factor_list(factor_body: Any) -> str | None:
        """从因子列表响应中提取第一个可用于筛选的 theme_key。

        请求参数:
            factor_body: 因子列表接口响应 JSON 解析结果。
        返回值:
            第一个存在的 theme_key；没有可用主题时返回 None。
        """
        if FactorListCompareService.factor_list_shape_errors(factor_body):
            return None

        for item in factor_body["data"]["items"]:
            for theme in item.get("themes") or []:
                theme_key = theme.get("theme_key")
                if theme_key:
                    return str(theme_key)
        return None

    @staticmethod
    def basic_field_mismatches(api_item: dict[str, Any], db_item: dict[str, Any], index: int) -> list[str]:
        """比较单条因子的基础字段。

        请求参数:
            api_item: 接口返回的单条因子数据。
            db_item: DB 查询出的单条因子数据。
            index: 当前数据在列表中的下标。
        返回值:
            字段不一致信息列表；空列表表示基础字段一致。
        """
        errors = []
        for field in FactorListCompareService.BASIC_FACTOR_FIELDS:
            if api_item.get(field) != db_item.get(field):
                errors.append(
                    f"items[{index}].{field} mismatch: api={api_item.get(field)!r}, db={db_item.get(field)!r}"
                )
        return errors

    @staticmethod
    def time_field_mismatches(api_item: dict[str, Any], db_item: dict[str, Any], index: int) -> list[str]:
        """比较单条因子的时间字段。

        请求参数:
            api_item: 接口返回的单条因子数据。
            db_item: DB 查询出的单条因子数据。
            index: 当前数据在列表中的下标。
        返回值:
            时间字段不一致或无法解析的信息列表；空列表表示时间字段一致。
        """
        errors = []
        for field in FactorListCompareService.TIME_FACTOR_FIELDS:
            try:
                api_value = FactorListCompareService.normalize_utc_second(api_item.get(field))
                db_value = FactorListCompareService.normalize_utc_second(db_item.get(field))
            except ValueError as exc:
                errors.append(f"items[{index}].{field} normalize failed: {exc}")
                continue
            if api_value != db_value:
                errors.append(f"items[{index}].{field} mismatch: api={api_value!r}, db={db_value!r}")
        return errors

    @staticmethod
    def factor_detail_mismatches(api_detail: dict[str, Any], db_detail: Any, index: int) -> list[str]:
        """比较单条因子的详情字段。

        请求参数:
            api_detail: 接口返回的 factor_detail 节点。
            db_detail: DB 查询出的 factor_detail 节点。
            index: 当前数据在列表中的下标。
        返回值:
            详情字段不一致信息列表；空列表表示详情一致。
        """
        errors = []
        if not isinstance(db_detail, dict):
            return [f"items[{index}].factor_detail db value must be dict"]

        for field in FactorListCompareService.DETAIL_FACTOR_FIELDS:
            if api_detail.get(field) != db_detail.get(field):
                errors.append(
                    f"items[{index}].factor_detail.{field} mismatch: "
                    f"api={api_detail.get(field)!r}, db={db_detail.get(field)!r}"
                )

        try:
            api_is_sub = FactorListCompareService.normalize_bool(api_detail.get("is_sub_factor_id"))
            db_is_sub = FactorListCompareService.normalize_bool(db_detail.get("is_sub_factor_id"))
        except ValueError as exc:
            errors.append(f"items[{index}].factor_detail.is_sub_factor_id normalize failed: {exc}")
        else:
            if api_is_sub != db_is_sub:
                errors.append(
                    f"items[{index}].factor_detail.is_sub_factor_id mismatch: "
                    f"api={api_is_sub!r}, db={db_is_sub!r}"
                )
        return errors

    @staticmethod
    def theme_mismatches(api_themes: list[dict[str, Any]], db_themes: Any, index: int) -> list[str]:
        """比较单条因子的主题归属字段。

        请求参数:
            api_themes: 接口返回的 themes 列表。
            db_themes: DB 查询出的 themes 列表。
            index: 当前数据在列表中的下标。
        返回值:
            主题归属不一致信息列表；空列表表示主题归属一致。
        """
        if not isinstance(db_themes, list):
            return [f"items[{index}].themes db value must be list"]

        api_by_id, api_errors = FactorListCompareService.themes_by_id(api_themes, f"items[{index}].themes api")
        db_by_id, db_errors = FactorListCompareService.themes_by_id(db_themes, f"items[{index}].themes db")
        errors = [*api_errors, *db_errors]
        if errors:
            return errors

        if set(api_by_id) != set(db_by_id):
            errors.append(f"items[{index}].theme_id set mismatch: api={sorted(api_by_id)}, db={sorted(db_by_id)}")
            return errors

        for theme_id, api_theme in api_by_id.items():
            db_theme = db_by_id[theme_id]
            for field in FactorListCompareService.THEME_FACTOR_FIELDS:
                if api_theme.get(field) != db_theme.get(field):
                    errors.append(
                        f"items[{index}].themes[{theme_id}].{field} mismatch: "
                        f"api={api_theme.get(field)!r}, db={db_theme.get(field)!r}"
                    )
        return errors

    @staticmethod
    def themes_by_id(themes: list[dict[str, Any]], label: str) -> tuple[dict[Any, dict[str, Any]], list[str]]:
        """把主题列表转换为按 id 索引的字典。

        请求参数:
            themes: 主题列表。
            label: 错误信息中使用的数据来源标签。
        返回值:
            二元组，第一项是按 id 索引的主题字典，第二项是转换过程中的错误信息列表。
        """
        result = {}
        errors = []
        for index, theme in enumerate(themes):
            if not isinstance(theme, dict):
                errors.append(f"{label}[{index}] item must be dict")
                continue
            theme_id = theme.get("id")
            if theme_id in result:
                errors.append(f"{label} duplicate theme_id: {theme_id!r}")
                continue
            result[theme_id] = theme
        return result, errors

    @staticmethod
    def extract_theme_items(data: Any) -> tuple[list[dict[str, Any]], list[str]]:
        """从主题列表响应 data 中提取主题数组。

        请求参数:
            data: 主题列表接口响应中的 data 节点。
        返回值:
            二元组，第一项是主题列表，第二项是错误信息列表。
        """
        if isinstance(data, list):
            return data, []
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items, []
            return [], ["themes data.items must be list"]
        return [], ["themes data must be list or dict with items"]

    @staticmethod
    def normalize_utc_second(value: Any) -> str | None:
        """把接口或 DB 时间值统一为 UTC 秒级字符串。

        请求参数:
            value: 接口或 DB 返回的时间值，支持 None、datetime 和常见字符串时间。
        返回值:
            UTC 秒级字符串；输入为 None 时返回 None。
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            text = value.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            text = text.replace(" ", "T", 1)
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
        else:
            raise ValueError(f"Unsupported datetime value: {value!r}")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def normalize_bool(value: Any) -> bool:
        """把接口或 DB 布尔值统一为 bool。

        请求参数:
            value: 接口或 DB 返回的布尔值，支持 bool、0 和 1。
        返回值:
            归一化后的 bool 值。
        """
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"is_sub_factor_id must be bool or 0/1, got {value!r}")
