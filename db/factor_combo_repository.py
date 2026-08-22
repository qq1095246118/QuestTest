"""组合因子台测试所需的 MySQL 数据访问与测试数据状态准备。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from db.client import DatabaseClient, DatabaseTransaction


_TEST_PARENT_FACTOR_PREFIX = "__questtest_unrelated_parent__"
_SIMULATED_PIPELINE_RUN_PREFIX = "legacy-simulated-form-"
_TEST_PARENT_EXCLUDED_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "latest_status_updated_at",
}


@dataclass(frozen=True)
class SubFactorChoice:
    """表示可用于组合因子测试的一个真实子因子。

    参数包含子因子主键、唯一名称及一个可用的母因子主键和名称。
    返回值由 ``FactorComboRepository`` 的因子选择方法产生，供表单和 Worker 组件构造使用。
    """

    sub_factor_id: int
    sub_factor_name: str
    parent_factor_id: int
    parent_factor_name: str


@dataclass(frozen=True)
class ParentFactorChoice:
    """表示至少关联多个子因子的真实母因子。

    参数包含母因子主键、唯一名称以及已排序的关联子因子。
    返回值由 ``find_parent_with_sub_factors`` 产生，供验证母因子展开规则使用。
    """

    factor_id: int
    factor_name: str
    sub_factors: tuple[SubFactorChoice, ...]


@dataclass(frozen=True)
class DetachedPoolMember:
    """保存被测试临时移出因子池的成员快照。"""

    row: dict[str, Any]


class FactorComboRepository:
    """封装组合因子测试的 MySQL 查询、受控状态准备和数据清理。"""

    def __init__(self, client: DatabaseClient, environment: str) -> None:
        """初始化组合因子数据仓储。

        参数 ``client`` 是已配置 MySQL 的 ``DatabaseClient``，``environment`` 是当前自动化环境名称。
        不返回值；所有写操作在执行前都会校验环境必须为 ``test``。
        """

        self._client = client
        self._environment = environment.strip().lower()
        self._test_parent_factor_ids_by_form: dict[int, set[int]] = {}

    def find_parent_with_sub_factors(self, minimum_sub_factors: int = 2) -> ParentFactorChoice | None:
        """查找一个拥有足够关联子因子的真实母因子及其全部关联子因子。

        参数 ``minimum_sub_factors`` 是所需最少关联子因子数；查询不依赖有效性评分，也不做数量截断。
        返回按子因子 ID 升序排列的 ``ParentFactorChoice``；测试库中不存在符合条件的母因子时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            SELECT DISTINCT
                f.id AS factor_id,
                f.factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factors AS f
            INNER JOIN factor_sub_factor_relations AS r
                ON r.factor_id = f.id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY f.id ASC, r.sub_factor_id ASC
            """
        )
        grouped: dict[int, list[SubFactorChoice]] = {}
        factor_names: dict[int, str] = {}
        for row in rows:
            factor_id = int(row["factor_id"])
            factor_names[factor_id] = str(row["factor_name"])
            grouped.setdefault(factor_id, []).append(
                SubFactorChoice(
                    sub_factor_id=int(row["sub_factor_id"]),
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=factor_id,
                    parent_factor_name=str(row["factor_name"]),
                )
            )
        for factor_id, sub_factors in grouped.items():
            if len(sub_factors) >= minimum_sub_factors:
                return ParentFactorChoice(
                    factor_id=factor_id,
                    factor_name=factor_names[factor_id],
                    sub_factors=tuple(sub_factors),
                )
        return None

    def find_sub_factor_pair(self) -> tuple[SubFactorChoice, SubFactorChoice] | None:
        """查找两个可用于表单提交的真实子因子。

        不接收参数。
        返回优先来自不同母因子的两个 ``SubFactorChoice``；测试库中不足两个可用子因子时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factor_sub_factor_relations AS r
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY r.factor_id ASC, r.sub_factor_id ASC
            """
        )
        choices: list[SubFactorChoice] = []
        seen_sub_factor_ids: set[int] = set()
        for row in rows:
            sub_factor_id = int(row["sub_factor_id"])
            if sub_factor_id in seen_sub_factor_ids:
                continue
            seen_sub_factor_ids.add(sub_factor_id)
            choices.append(
                SubFactorChoice(
                    sub_factor_id=sub_factor_id,
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=int(row["parent_factor_id"]),
                    parent_factor_name=str(row["parent_factor_name"]),
                )
            )
        for first_index, first_choice in enumerate(choices):
            for second_choice in choices[first_index + 1 :]:
                if second_choice.parent_factor_id != first_choice.parent_factor_id:
                    return first_choice, second_choice
        if len(choices) >= 2:
            return choices[0], choices[1]
        return None

    def find_sub_factor_outside_pool(self, pool_id: int) -> SubFactorChoice | None:
        """查找一个不属于指定锁定池的真实子因子。

        参数 ``pool_id`` 是因子池主键。
        返回一个 ``SubFactorChoice``；没有可用池外子因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                sf.id AS sub_factor_id,
                sf.sub_factor_name
            FROM sub_factors AS sf
            INNER JOIN factor_sub_factor_relations AS r
                ON r.sub_factor_id = sf.id
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM factor_combo_pool_member AS member
                WHERE member.pool_id = %s
                  AND member.sub_factor_id = sf.id
            )
            ORDER BY sf.id ASC
            LIMIT 1
            """,
            (pool_id,),
        )
        return self._to_sub_factor_choice(row)

    def find_unrelated_parent_factor(self, sub_factor_id: int, excluded_factor_id: int) -> int | None:
        """查找一个与指定子因子没有父子关联的真实母因子。

        参数 ``sub_factor_id`` 是目标子因子主键，``excluded_factor_id`` 是已知合法父因子主键。
        返回不关联该子因子的母因子主键；没有符合条件的母因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT f.id AS factor_id
            FROM factors AS f
            WHERE f.id <> %s
              AND LEFT(f.factor_name, CHAR_LENGTH(%s)) <> %s
              AND NOT EXISTS (
                SELECT 1
                FROM factor_sub_factor_relations AS relation_item
                WHERE relation_item.factor_id = f.id
                  AND relation_item.sub_factor_id = %s
              )
            ORDER BY f.id ASC
            LIMIT 1
            """,
            (excluded_factor_id, _TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX, sub_factor_id),
        )
        return int(row["factor_id"]) if row is not None else None

    @contextmanager
    def temporarily_detach_pool_member(
        self,
        form_id: int,
        sub_factor_id: int,
    ) -> Iterator[DetachedPoolMember]:
        """在一次接口调用期间临时移出测试表单的一个因子池成员。

        参数 ``form_id`` 是当前测试创建的组合表单主键，``sub_factor_id`` 是该表单池中的子因子主键。
        返回上下文管理器，内容是被移出的成员快照；离开上下文时无论接口调用成功或抛出异常都会恢复原成员。
        仅允许测试环境写入；成员不存在、池归属不一致、快照字段缺失或恢复失败时抛出 ``RuntimeError``，不会静默跳过。
        """

        self._assert_test_write_allowed()
        normalized_form_id = int(form_id)
        normalized_sub_factor_id = int(sub_factor_id)
        with self._client.transaction() as transaction:
            row = transaction.fetch_one(
                """
                SELECT
                    member.*,
                    form.factor_combo_pool_id AS form_pool_id
                FROM factor_combo_pool_member AS member
                INNER JOIN factor_combo_form AS form
                    ON form.id = member.factor_combo_form_id
                WHERE member.factor_combo_form_id = %s
                  AND member.sub_factor_id = %s
                FOR UPDATE
                """,
                (normalized_form_id, normalized_sub_factor_id),
            )
            if row is None:
                raise RuntimeError(
                    f"Pool member does not belong to test form: form={normalized_form_id}, "
                    f"sub_factor={normalized_sub_factor_id}"
                )
            if row.get("form_pool_id") is None or int(row["pool_id"]) != int(row["form_pool_id"]):
                raise RuntimeError(
                    f"Pool member pool does not match test form: form={normalized_form_id}, "
                    f"sub_factor={normalized_sub_factor_id}"
                )
            if row.get("id") is None:
                raise RuntimeError("Pool member snapshot is missing its primary key")
            required_columns = (
                "factor_combo_form_id",
                "pool_id",
                "sub_factor_id",
                "factor_detail_id",
                "created_by",
                "updated_by",
            )
            missing_columns = [column for column in required_columns if column not in row]
            if missing_columns:
                raise RuntimeError(f"Pool member snapshot is missing columns: {missing_columns}")
            result = transaction.execute(
                "DELETE FROM factor_combo_pool_member WHERE id = %s",
                (int(row["id"]),),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Pool member could not be detached: {row['id']}")
            snapshot = DetachedPoolMember(row=dict(row))
        try:
            yield snapshot
        finally:
            self._restore_detached_pool_member(snapshot)

    def ensure_unrelated_parent_factor_for_test(
        self,
        form_id: int,
        sub_factor_id: int,
        excluded_factor_id: int,
    ) -> int:
        """取得或创建一个与指定子因子无关系的测试母因子。

        参数 ``form_id`` 是将使用该母因子的测试表单主键，``sub_factor_id`` 是目标池内子因子主键，
        ``excluded_factor_id`` 是当前已知父因子主键。优先返回数据库中已有的不相关母因子；找不到时仅在测试环境
        创建带 ``__questtest_unrelated_parent__`` 标记的临时母因子，并登记到表单资源图中，返回母因子主键。
        数据库写入失败或无法确认新记录主键时抛出 ``RuntimeError``，不返回 ``None``。
        """

        existing_factor_id = self.find_unrelated_parent_factor(sub_factor_id, excluded_factor_id)
        if existing_factor_id is not None:
            return existing_factor_id

        self._assert_test_write_allowed()
        token = uuid4().hex
        factor_name = f"{_TEST_PARENT_FACTOR_PREFIX}{token}"
        serial_number = f"questtest-parent-{token}"
        metadata = json.dumps(
            {"questtest": True, "purpose": "unrelated-parent-factor", "form_id": int(form_id)},
            separators=(",", ":"),
        )
        with self._client.transaction() as transaction:
            source = transaction.fetch_one(
                """
                SELECT *
                FROM factors
                WHERE id <> %s
                  AND LEFT(factor_name, CHAR_LENGTH(%s)) <> %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(excluded_factor_id), _TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX),
            )
            if source is None:
                # 极小测试库可能只有当前母因子；克隆它仍能产生新的、与目标子因子无关系的母因子 ID。
                source = transaction.fetch_one(
                    """
                    SELECT *
                    FROM factors
                    WHERE LEFT(factor_name, CHAR_LENGTH(%s)) <> %s
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (_TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX),
                )
            if source is None:
                raise RuntimeError("Cannot create a test parent factor because factors has no source row")
            insert_columns = self._factor_insert_columns(transaction, source)
            required_columns = {"factor_name", "cn_name", "serial_number"}
            missing_columns = sorted(required_columns - set(insert_columns))
            if missing_columns:
                raise RuntimeError(f"Factors schema is missing required columns: {missing_columns}")
            values_by_column: dict[str, Any] = {}
            for column in insert_columns:
                value = source.get(column)
                if column in {"factor_name", "cn_name"}:
                    value = factor_name
                elif column == "serial_number":
                    value = serial_number
                elif column == "serial_prefix":
                    value = "questtest"
                elif column in {"parent_factor_id", "parent_factor", "parent_factor_name"}:
                    value = None
                elif column == "factor_theme":
                    value = "__questtest__"
                elif column == "metadata":
                    value = metadata
                elif column == "level":
                    value = 1
                elif column == "child_factor_count":
                    value = 0
                elif column == "max_level":
                    value = 1
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value, separators=(",", ":"))
                values_by_column[column] = value
            insert_columns = tuple(values_by_column)
            placeholders = self._placeholders(insert_columns)
            result = transaction.execute(
                f"INSERT INTO factors ({', '.join(insert_columns)}) VALUES ({placeholders})",
                tuple(values_by_column[column] for column in insert_columns),
            )
            factor_id = result.lastrowid
            if factor_id is None:
                row = transaction.fetch_one(
                    "SELECT id FROM factors WHERE factor_name = %s",
                    (factor_name,),
                )
                factor_id = row.get("id") if row is not None else None
            if factor_id is None:
                raise RuntimeError(f"Created test parent factor cannot be located: {factor_name}")
        normalized_factor_id = int(factor_id)
        self._test_parent_factor_ids_by_form.setdefault(int(form_id), set()).add(normalized_factor_id)
        return normalized_factor_id

    @staticmethod
    def _factor_insert_columns(
        transaction: DatabaseTransaction,
        source: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """读取 ``factors`` 的可插入字段，避免临时数据依赖固定版本的列清单。

        参数 ``transaction`` 是当前测试事务，``source`` 是用于克隆的母因子记录。
        返回经过数据库字段元数据和标识符校验的列名；自增列、时间自动生成列和生成列会被排除。
        字段元数据缺失或返回非法列名时抛出 ``RuntimeError``，不执行不确定的动态 SQL。
        """

        metadata_rows = transaction.fetch_all("SHOW COLUMNS FROM factors")
        if not metadata_rows:
            raise RuntimeError("Factors schema metadata is empty")
        source_columns = set(source)
        columns: list[str] = []
        for metadata in metadata_rows:
            column = metadata.get("Field") or metadata.get("field")
            if not isinstance(column, str) or not column:
                raise RuntimeError(f"Factors schema metadata has no valid column name: {metadata}")
            if not (column[0].isalpha() or column[0] == "_") or not all(
                character.isalnum() or character == "_" for character in column
            ):
                raise RuntimeError(f"Factors schema metadata has an unsafe column name: {column!r}")
            extra = str(metadata.get("Extra") or metadata.get("extra") or "").lower()
            if column in _TEST_PARENT_EXCLUDED_COLUMNS:
                continue
            if "auto_increment" in extra or "generated" in extra:
                continue
            if column in source_columns:
                columns.append(column)
        if not columns:
            raise RuntimeError("Factors schema has no insertable columns shared with the source row")
        return tuple(dict.fromkeys(columns))

    def _restore_detached_pool_member(self, snapshot: DetachedPoolMember) -> None:
        """恢复一次测试临时移出的因子池成员。"""

        row = snapshot.row
        required_columns = (
            "factor_combo_form_id",
            "pool_id",
            "sub_factor_id",
            "factor_detail_id",
            "created_by",
            "updated_by",
        )
        missing_columns = [column for column in required_columns if column not in row]
        if missing_columns:
            raise RuntimeError(f"Pool member restore is missing columns: {missing_columns}")
        columns = (
            "factor_combo_form_id",
            "pool_id",
            "sub_factor_id",
            "factor_detail_id",
            "metrics_snapshot_json",
            "validity_snapshot_json",
            "created_by",
            "created_at",
            "updated_by",
            "updated_at",
            "definition_snapshot_json",
            "sort_order",
        )
        available_columns = tuple(column for column in columns if column in row)
        placeholders = self._placeholders(available_columns)
        values: list[Any] = []
        for column in available_columns:
            value = row[column]
            if column.endswith("_json") and isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"))
            values.append(value)

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            existing = transaction.fetch_one(
                """
                SELECT id, factor_combo_form_id
                FROM factor_combo_pool_member
                WHERE pool_id = %s
                  AND sub_factor_id = %s
                LIMIT 1
                """,
                (int(row["pool_id"]), int(row["sub_factor_id"])),
            )
            if existing is not None:
                if int(existing["factor_combo_form_id"]) != int(row["factor_combo_form_id"]):
                    raise RuntimeError(
                        "Detached pool member was re-created under a different form: "
                        f"sub_factor={row['sub_factor_id']}"
                    )
                update_columns = tuple(column for column in available_columns if column != "id")
                if update_columns:
                    assignments = ", ".join(f"{column} = %s" for column in update_columns)
                    update_values = tuple(
                        values[available_columns.index(column)] for column in update_columns
                    )
                    transaction.execute(
                        f"""
                        UPDATE factor_combo_pool_member
                        SET {assignments}
                        WHERE id = %s
                        """,
                        update_values + (int(existing["id"]),),
                    )
                return
            result = transaction.execute(
                f"""
                INSERT INTO factor_combo_pool_member ({', '.join(available_columns)})
                VALUES ({placeholders})
                """,
                tuple(values),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Detached pool member could not be restored: {row['sub_factor_id']}")

    def get_form(self, form_id: int) -> dict[str, Any] | None:
        """读取组合研究表单及其关联指针。

        参数 ``form_id`` 是 ``factor_combo_form`` 主键。
        返回标准化后的表单字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT form.*
            FROM factor_combo_form AS form
            WHERE form.id = %s
            """,
            (form_id,),
        )
        return self._normalize_database_row(row)

    def get_work_order_data_spec(self, form_id: int) -> dict[str, Any] | None:
        """读取表单中明确持久化的 Work Order ``data_spec`` 快照。

        参数 ``form_id`` 是 ``factor_combo_form`` 主键。返回数据库明确保存的 ``data_spec`` 对象；优先读取表单
        记录中的专用 ``data_spec``/``data_spec_json``/``work_order_json`` 字段，其次读取 ``form_json.data_spec``。
        如果数据库只保存了请求配置而没有保存 Work Order 快照则返回 ``None``，调用方必须将其报告为契约缺口，
        不在 Repository 中根据默认值猜测接口返回内容。
        """

        form = self.get_form(form_id)
        if form is None:
            return None
        for field_name in ("data_spec", "data_spec_json", "work_order_json"):
            value = form.get(field_name)
            if isinstance(value, Mapping):
                return dict(value)
            if isinstance(value, str) and value.strip():
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError as error:
                    raise RuntimeError(f"Persisted work order {field_name} is invalid JSON") from error
                if not isinstance(parsed, Mapping):
                    raise RuntimeError(f"Persisted work order {field_name} must be a JSON object")
                return dict(parsed)
        form_json = form.get("form_json")
        if isinstance(form_json, Mapping) and isinstance(form_json.get("data_spec"), Mapping):
            return dict(form_json["data_spec"])
        return None

    def count_forms_for_session(self, session_id: int) -> int:
        """统计指定会话下的组合研究表单数量。

        参数 ``session_id`` 是 Chat Session 主键。
        返回表单记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_form WHERE session_id = %s",
            (session_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_pool(self, pool_id: int) -> dict[str, Any] | None:
        """读取一个组合因子池的状态和快照。

        参数 ``pool_id`` 是 ``factor_combo_pool.pool_id``。
        返回标准化后的因子池字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT pool.*
            FROM factor_combo_pool AS pool
            WHERE pool.pool_id = %s
            """,
            (pool_id,),
        )
        return self._normalize_database_row(row)

    def get_pool_members(self, form_id: int) -> list[dict[str, Any]]:
        """读取一个表单锁定池的全部成员及对应母因子。

        参数 ``form_id`` 是组合研究表单主键。
        返回按 ``sort_order`` 排序的成员字典列表；表单没有因子池成员时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                member.*,
                member.id AS member_id,
                member.factor_combo_form_id AS member_form_id,
                member.pool_id AS member_pool_id,
                member.factor_detail_id AS member_factor_detail_id,
                member.sort_order AS member_sort_order,
                member.definition_snapshot_json AS member_definition_snapshot_json,
                member.metrics_snapshot_json AS member_metrics_snapshot_json,
                member.validity_snapshot_json AS member_validity_snapshot_json,
                sf.sub_factor_name,
                sf.cn_name AS sub_factor_cn_name,
                sf.serial_number AS sub_factor_serial_number,
                sf.factor_bar_interval AS sub_factor_bar_interval,
                detail.id AS factor_detail_record_id,
                detail.factor_id AS factor_detail_factor_id,
                detail.is_sub_factor_id AS factor_detail_is_sub_factor_id,
                detail.name AS factor_detail_name,
                detail.serial_number AS factor_detail_serial_number,
                detail.status AS factor_detail_status,
                detail.updated_at AS factor_detail_updated_at,
                (
                    SELECT MIN(relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_id,
                (
                    SELECT GROUP_CONCAT(
                        DISTINCT relation_item.factor_id
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_ids,
                (
                    SELECT GROUP_CONCAT(
                        parent_item.factor_name
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    INNER JOIN factors AS parent_item
                        ON parent_item.id = relation_item.factor_id
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_names,
                (
                    SELECT GROUP_CONCAT(
                        parent_item.serial_number
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    INNER JOIN factors AS parent_item
                        ON parent_item.id = relation_item.factor_id
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_serial_numbers,
                (
                    SELECT COUNT(*)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_relation_count,
                (
                    SELECT COUNT(DISTINCT relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_distinct_count,
                parent.factor_name AS parent_factor_name,
                parent.cn_name AS parent_factor_cn_name,
                parent.serial_number AS parent_factor_serial_number
            FROM factor_combo_pool_member AS member
            INNER JOIN sub_factors AS sf
                ON sf.id = member.sub_factor_id
            LEFT JOIN factors_details AS detail
                ON detail.id = member.factor_detail_id
            LEFT JOIN factors AS parent
                ON parent.id = (
                    SELECT MIN(relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                )
            WHERE member.factor_combo_form_id = %s
            ORDER BY member.sort_order ASC, member.id ASC
            """,
            (form_id,),
        )
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized = self._normalize_database_row(row) or {}
            parent_ids = normalized.get("parent_factor_ids")
            if isinstance(parent_ids, str):
                normalized["parent_factor_ids"] = [
                    int(value)
                    for value in parent_ids.split(",")
                    if value.strip().isdigit()
                ]
            for source_name, target_name in (
                ("parent_factor_names", "parent_factor_names"),
                ("parent_factor_serial_numbers", "parent_factor_serial_numbers"),
            ):
                value = normalized.get(source_name)
                if isinstance(value, str):
                    normalized[target_name] = [item for item in value.split(",") if item.strip()]
            # 这些 canonical 别名保留独立名称，避免上层把 member.id、detail.id 或版本业务 ID 混为一谈。
            normalized.setdefault("factor_combo_form_id", normalized.get("member_form_id"))
            normalized.setdefault("pool_id", normalized.get("member_pool_id"))
            normalized.setdefault("factor_detail_id", normalized.get("member_factor_detail_id"))
            normalized_rows.append(normalized)
        return normalized_rows

    def get_combo_version(self, version_id: int) -> dict[str, Any] | None:
        """读取一个具体组合版本。

        参数 ``version_id`` 是 ``factor_combo.id``，不是业务级 ``combo_id``。
        返回组合版本字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT version.*
            FROM factor_combo AS version
            WHERE version.id = %s
            """,
            (version_id,),
        )
        return self._normalize_database_row(row)

    def count_versions_for_form(self, form_id: int) -> int:
        """统计指定表单产生的组合版本数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回版本数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            """
            SELECT COUNT(*) AS record_count
            FROM factor_combo
            WHERE initial_form_id = %s
               OR combo_family_key = CONCAT('factor-combo-form:', %s)
            """,
            (form_id, form_id),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_components(self, version_id: int) -> int:
        """统计具体组合版本的组件数量。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_component WHERE combo_id = %s",
            (version_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_components(self, version_id: int) -> list[dict[str, Any]]:
        """读取一个组合版本的全部组件。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件字典列表；未写入组件时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                component.*,
                factor_item.factor_name,
                factor_item.cn_name AS factor_cn_name,
                sub_factor_item.sub_factor_name,
                sub_factor_item.cn_name AS sub_factor_cn_name,
                sub_factor_item.serial_number AS sub_factor_serial_number
            FROM factor_combo_component AS component
            INNER JOIN factors AS factor_item
                ON factor_item.id = component.component_factor_id
            INNER JOIN sub_factors AS sub_factor_item
                ON sub_factor_item.id = component.component_sub_factor_id
            WHERE component.combo_id = %s
            ORDER BY
                component.component_sub_factor_id ASC,
                component.component_factor_id ASC,
                component.id ASC
            """,
            (version_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_experiment(self, experiment_info_id: int) -> dict[str, Any] | None:
        """读取一个组合因子实验信息记录。

        参数 ``experiment_info_id`` 是 ``factor_combo_experiment_info.id``。
        返回实验字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT experiment.*
            FROM factor_combo_experiment_info AS experiment
            WHERE experiment.id = %s
            """,
            (experiment_info_id,),
        )
        return self._normalize_database_row(row)

    def get_experiment_by_external_id(self, experiment_id: str) -> dict[str, Any] | None:
        """按接口幂等键读取组合实验记录。

        参数 ``experiment_id`` 是实验写入接口路径中的业务标识。
        返回实验字典；不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT experiment.*
            FROM factor_combo_experiment_info AS experiment
            WHERE experiment.experiment_id = %s
            """,
            (experiment_id,),
        )
        return self._normalize_database_row(row)

    def count_experiments_by_artifact_uri(self, artifact_uri: str) -> int:
        """统计指定 Artifact URI 关联的实验记录数。

        参数 ``artifact_uri`` 是实验产物 URI。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_uri = %s",
            (artifact_uri,),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_experiments_by_artifact_hash(self, artifact_sha256: str) -> int:
        """统计指定 Artifact SHA256 对应的实验记录数。

        参数 ``artifact_sha256`` 是十六进制内容摘要。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_hash = %s",
            (artifact_sha256.lower(),),
        )
        return int(row["record_count"]) if row is not None else 0

    def find_existing_local_artifact(self) -> dict[str, Any] | None:
        """查找一个已有的绝对路径 Artifact，供 SHA256 冲突场景复用其可读内容。

        不接收参数。返回包含 ``artifact_uri`` 和 ``artifact_hash`` 的已有实验字典；测试库没有绝对路径 Artifact
        时返回 ``None``。该方法只读数据库，不验证文件本身，文件可读性由实验接口在真实请求中验证。
        """

        row = self._client.fetch_one(
            """
            SELECT artifact_uri, artifact_hash
            FROM factor_combo_experiment_info
            WHERE artifact_uri LIKE '/%'
              AND artifact_hash IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return self._normalize_database_row(row)

    def get_feedback(self, feedback_id: int) -> dict[str, Any] | None:
        """读取组合报告反馈及下一轮关联指针。

        参数 ``feedback_id`` 是 ``factor_combo_experiment_feedback`` 主键。
        返回反馈字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT feedback.*
            FROM factor_combo_experiment_feedback AS feedback
            WHERE feedback.id = %s
            """,
            (feedback_id,),
        )
        return self._normalize_database_row(row)

    def count_feedback_for_form(self, form_id: int) -> int:
        """统计一个表单的反馈记录数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回反馈记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_feedback WHERE form_id = %s",
            (form_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_registration(
        self,
        combo_id: int,
        version_id: int | None = None,
        combo_version_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """按组合业务标识或具体版本读取唯一登记完成标记。

        参数 ``combo_id`` 是组合业务标识，``version_id`` 是具体 ``factor_combo.id``，``combo_version_hash`` 是版本内容
        哈希。登记表的 ``combo_id`` 按新版契约关联 ``factor_combo.combo_id``，再以版本哈希定位具体版本；可选版本主键
        用于进一步限定目标。查询最多读取两条结果；若身份条件仍命中多条登记记录则抛出 ``RuntimeError``，不会静默
        取最新一条。
        返回带有 ``version_id``、``version_business_id`` 和 ``version_combo_version_hash`` canonical 字段的登记字典；
        未找到时返回 ``None``。
        """

        predicates = [
            "version.combo_id = %s",
            "registered.combo_id = version.combo_id",
            "registered.combo_version_hash = version.combo_version_hash",
        ]
        parameters: list[Any] = [int(combo_id)]
        if version_id is not None:
            predicates.append("version.id = %s")
            parameters.append(int(version_id))
        if combo_version_hash is not None:
            predicates.append("registered.combo_version_hash = %s")
            parameters.append(str(combo_version_hash).strip().lower())
        rows = self._client.fetch_all(
            f"""
            SELECT
                registered.*,
                version.id AS version_id,
                version.combo_id AS version_business_id,
                version.combo_version_hash AS version_combo_version_hash
            FROM factor_combo_registered_factor AS registered
            INNER JOIN factor_combo AS version
                ON {' AND '.join(predicates)}
            ORDER BY registered.id ASC
            LIMIT 2
            """,
            tuple(parameters),
        )
        if len(rows) > 1:
            raise RuntimeError(
                "Multiple factor combo registrations match the same concrete version identity: "
                f"combo_id={combo_id}, version_id={version_id}, combo_version_hash={combo_version_hash!r}"
            )
        return self._normalize_database_row(rows[0] if rows else None)

    def get_registered_sub_factor(self, sub_factor_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的复合子因子。

        参数 ``sub_factor_id`` 是登记响应中的子因子主键。
        返回子因子核心字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT sub_factor.*
            FROM sub_factors AS sub_factor
            WHERE sub_factor.id = %s
            """,
            (sub_factor_id,),
        )
        return self._normalize_database_row(row)

    def get_registered_factor_detail(self, factor_detail_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的因子详情。

        参数 ``factor_detail_id`` 是登记响应中的详情主键。
        返回因子详情核心字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT detail.*
            FROM factors_details AS detail
            WHERE detail.id = %s
            """,
            (factor_detail_id,),
        )
        return self._normalize_database_row(row)

    def get_registered_validity_status(self, validity_status_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的有效性快照。

        参数 ``validity_status_id`` 是登记响应中的有效性记录主键。
        返回有效性和审计字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT validity.*
            FROM factor_validity_status AS validity
            WHERE validity.id = %s
            """,
            (validity_status_id,),
        )
        return self._normalize_database_row(row)

    def get_factor_refresh_calculation_runs(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子在新版指标汇总表中的计算 Run 证据。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回按更新时间倒序聚合的 ``factor_ic_summary_metrics`` 记录；每行包含计算 Run 状态、汇总行数和至少一个
        非空 IC/ICIR/t-stat/有效性评分指标的行数。该方法只读权威新版指标表 ``factor_ic_summary_metrics`` 及其 Run 主表
        ``factor_ic_runs``，不会读取已废弃的 ``factor_mining_symbol_window_metric``，数据库异常直接向调用方抛出。
        """

        return self._client.fetch_all(
            """
            SELECT
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status AS run_status,
                COUNT(*) AS summary_row_count,
                SUM(
                    CASE
                        WHEN summary.coverage_mean IS NOT NULL
                          OR summary.coverage_min IS NOT NULL
                          OR summary.mean_ic IS NOT NULL
                          OR summary.median_ic IS NOT NULL
                          OR summary.std_ic IS NOT NULL
                          OR summary.icir IS NOT NULL
                          OR summary.mean_abs_ic IS NOT NULL
                          OR summary.positive_ic_rate IS NOT NULL
                          OR summary.mean_rank_ic IS NOT NULL
                          OR summary.median_rank_ic IS NOT NULL
                          OR summary.std_rank_ic IS NOT NULL
                          OR summary.rank_icir IS NOT NULL
                          OR summary.mean_abs_rank_ic IS NOT NULL
                          OR summary.positive_rank_ic_rate IS NOT NULL
                          OR summary.ic_t_stat IS NOT NULL
                          OR summary.rank_ic_t_stat IS NOT NULL
                          OR summary.monotonicity_ratio IS NOT NULL
                          OR summary.mean_long_short_return IS NOT NULL
                          OR summary.long_short_annual_return IS NOT NULL
                          OR summary.long_short_t_stat IS NOT NULL
                          OR summary.is_icir IS NOT NULL
                          OR summary.oos_icir IS NOT NULL
                          OR summary.icir_oos_retention IS NOT NULL
                          OR summary.rank_is_icir IS NOT NULL
                          OR summary.rank_oos_icir IS NOT NULL
                          OR summary.rank_icir_oos_retention IS NOT NULL
                          OR summary.slice_count IS NOT NULL
                          OR summary.valid_slice_count IS NOT NULL
                          OR summary.is_period_start IS NOT NULL
                          OR summary.is_period_end IS NOT NULL
                          OR summary.oos_period_start IS NOT NULL
                          OR summary.oos_period_end IS NOT NULL
                          OR summary.is_slice_count IS NOT NULL
                          OR summary.oos_slice_count IS NOT NULL
                          OR summary.ic_score IS NOT NULL
                          OR summary.rank_ic_score IS NOT NULL
                          OR summary.icir_score IS NOT NULL
                          OR summary.rank_icir_score IS NOT NULL
                          OR summary.t_stat_score IS NOT NULL
                          OR summary.oos_retention_score IS NOT NULL
                          OR summary.monotonicity_score IS NOT NULL
                          OR summary.long_short_score IS NOT NULL
                          OR summary.final_score IS NOT NULL
                          OR summary.mean_stratification IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS populated_metric_row_count,
                COUNT(DISTINCT summary.ic_scope) AS ic_scope_count,
                MAX(summary.updated_at) AS latest_updated_at
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            GROUP BY
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status
            ORDER BY latest_updated_at DESC, summary.run_id DESC
            """,
            (sub_factor_id,),
        )

    def get_factor_refresh_calculation_metrics(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子的新版 IC 汇总明细及其计算 Run 状态。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回 ``factor_ic_summary_metrics`` 的明细行，包含完整汇总字段、``summary_id`` 别名和关联的
        ``factor_ic_runs.status``；调用方可据此按有效性快照引用的 Run/summary 精确筛选本次刷新结果。
        方法只读新版指标表和 Run 主表，不读取已废弃的 ``factor_mining_symbol_window_metric``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                summary.*,
                summary.id AS summary_id,
                runs.status AS run_status,
                runs.id AS run_record_id,
                runs.run_name AS run_name,
                runs.window_config_version AS run_window_config_version,
                runs.scoring_version AS run_scoring_version,
                runs.interval_value AS run_interval_value,
                runs.forward_return_horizon AS run_forward_return_horizon,
                runs.universe_key AS run_universe_key,
                runs.weighting_method AS run_weighting_method,
                runs.weight_lookback_days AS run_weight_lookback_days,
                runs.method AS run_method,
                runs.data_start AS run_data_start,
                runs.data_end AS run_data_end,
                runs.config_hash AS run_config_hash,
                runs.config_json AS run_config_json,
                runs.created_at AS run_created_at
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            ORDER BY summary.updated_at DESC, summary.id DESC
            """,
            (sub_factor_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_factor_refresh_run_details(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取目标子因子所有新版指标 Run 的完整主表记录。

        参数 ``sub_factor_id`` 是登记后生成的复合子因子主键。
        返回按创建时间倒序排列的 ``factor_ic_runs`` 完整记录列表；只返回至少有一条汇总指标的 Run，数据库查询或
        JSON 解析异常直接向调用方抛出。该方法用于核对 Run 的配置身份、状态和汇总记录归属，不读取旧版指标表。
        """

        rows = self._client.fetch_all(
            """
            SELECT DISTINCT runs.*
            FROM factor_ic_runs AS runs
            INNER JOIN factor_ic_summary_metrics AS summary
                ON summary.run_id = runs.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            ORDER BY runs.created_at DESC, runs.id DESC
            """,
            (sub_factor_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_factor_refresh_validity_snapshots(
        self,
        sub_factor_id: int,
        registration_validity_status_id: int,
    ) -> list[dict[str, Any]]:
        """读取登记初始快照之外、且引用新版汇总结果的有效性快照。

        参数 ``sub_factor_id`` 是登记生成的复合子因子主键，``registration_validity_status_id`` 是登记响应中的初始
        ``factor_validity_status.id``。返回关联的有效性快照及其时序/截面汇总记录身份；查询排除登记初始快照和
        没有任何 summary 外键的登记占位状态；如果后端在原地更新登记快照并补上 summary 外键，该行会被保留。方法只读
        数据库，SQL 或连接异常直接向调用方抛出。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                validity.*,
                validity.universe_key,
                validity.factor_bar_interval,
                validity.factor_window_bars,
                validity.return_bar_interval,
                validity.forward_return_bars,
                validity.window_scope,
                validity.period_start,
                validity.period_end,
                CASE WHEN validity.id = %s THEN 1 ELSE 0 END AS is_registration_snapshot,
                time_summary.run_id AS time_series_summary_run_id,
                time_summary.factor_id AS time_series_summary_factor_id,
                time_summary.is_sub_factor_id AS time_series_summary_is_sub_factor_id,
                time_summary.ic_scope AS time_series_summary_ic_scope,
                time_summary.calculation_mode AS time_series_summary_calculation_mode,
                time_summary.factor_bar_interval AS time_series_summary_factor_bar_interval,
                time_summary.factor_window_bars AS time_series_summary_factor_window_bars,
                time_summary.return_bar_interval AS time_series_summary_return_bar_interval,
                time_summary.forward_return_bars AS time_series_summary_forward_return_bars,
                time_summary.universe_key AS time_series_summary_universe_key,
                time_summary.symbol AS time_series_summary_symbol,
                time_summary.window_scope AS time_series_summary_window_scope,
                time_summary.metric_window_bars AS time_series_summary_metric_window_bars,
                time_summary.metric_window_days AS time_series_summary_metric_window_days,
                time_summary.period_start AS time_series_summary_period_start,
                time_summary.period_end AS time_series_summary_period_end,
                cross_summary.run_id AS cross_sectional_summary_run_id,
                cross_summary.factor_id AS cross_sectional_summary_factor_id,
                cross_summary.is_sub_factor_id AS cross_sectional_summary_is_sub_factor_id,
                cross_summary.ic_scope AS cross_sectional_summary_ic_scope,
                cross_summary.calculation_mode AS cross_sectional_summary_calculation_mode,
                cross_summary.factor_bar_interval AS cross_sectional_summary_factor_bar_interval,
                cross_summary.factor_window_bars AS cross_sectional_summary_factor_window_bars,
                cross_summary.return_bar_interval AS cross_sectional_summary_return_bar_interval,
                cross_summary.forward_return_bars AS cross_sectional_summary_forward_return_bars,
                cross_summary.universe_key AS cross_sectional_summary_universe_key,
                cross_summary.symbol AS cross_sectional_summary_symbol,
                cross_summary.window_scope AS cross_sectional_summary_window_scope,
                cross_summary.metric_window_bars AS cross_sectional_summary_metric_window_bars,
                cross_summary.metric_window_days AS cross_sectional_summary_metric_window_days,
                cross_summary.period_start AS cross_sectional_summary_period_start,
                cross_summary.period_end AS cross_sectional_summary_period_end
            FROM factor_validity_status AS validity
            LEFT JOIN factor_ic_summary_metrics AS time_summary
                ON time_summary.id = validity.time_series_summary_id
            LEFT JOIN factor_ic_summary_metrics AS cross_summary
                ON cross_summary.id = validity.cross_sectional_summary_id
            WHERE validity.factor_id = %s
              AND validity.is_sub_factor_id = 1
              AND (
                  validity.time_series_summary_id IS NOT NULL
                  OR validity.cross_sectional_summary_id IS NOT NULL
              )
            ORDER BY validity.updated_at DESC, validity.id DESC
            """,
            (registration_validity_status_id, sub_factor_id),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def count_parent_relations_for_sub_factor(self, sub_factor_id: int) -> int:
        """统计登记生成子因子的母因子关联数量。

        参数 ``sub_factor_id`` 是组合报告登记接口生成的子因子主键。
        返回 ``factor_sub_factor_relations`` 中对应行数；调用方根据当前业务规则判断数量和来源是否符合预期。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_sub_factor_relations WHERE sub_factor_id = %s",
            (sub_factor_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def find_existing_sub_factor_name(self) -> str | None:
        """查找一个已存在的非空子因子名称用于名称冲突测试。

        不接收参数。
        返回一个子因子名称；表为空时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT sub_factor_name
            FROM sub_factors
            WHERE sub_factor_name IS NOT NULL
              AND TRIM(sub_factor_name) <> ''
            ORDER BY id ASC
            LIMIT 1
            """
        )
        return str(row["sub_factor_name"]) if row is not None else None

    def prepare_form_for_worker(self, form_id: int, pipeline_run_id: str, lock_pool: bool = True) -> dict[str, Any]:
        """将测试创建的表单准备为初始版本 Worker 回写前置状态。

        参数 ``form_id`` 是测试表单主键，``pipeline_run_id`` 是本次模拟 Worker 运行标识，``lock_pool`` 决定是否锁定因子池。
        返回更新后的表单字典；仅允许测试环境写入，表单或因子池不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            form = transaction.fetch_one(
                """
                SELECT id, factor_combo_pool_id
                FROM factor_combo_form
                WHERE id = %s
                FOR UPDATE
                """,
                (form_id,),
            )
            if form is None:
                raise RuntimeError(f"Factor combo form does not exist: {form_id}")
            pool_id = int(form["factor_combo_pool_id"])
            if lock_pool:
                result = transaction.execute(
                    "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
                    ("locked", pool_id),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
            transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    pipeline_run_id = %s,
                    factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE id = %s
                """,
                ("processing", pipeline_run_id, form_id),
            )
        prepared_form = self.get_form(form_id)
        if prepared_form is None:
            raise RuntimeError(f"Prepared factor combo form cannot be read: {form_id}")
        return prepared_form

    def set_form_status_for_test(self, form_id: int, status: str) -> dict[str, Any]:
        """只修改测试表单状态以构造单一前置条件冲突。

        参数 ``form_id`` 是自动化创建的表单主键，``status`` 必须是表结构允许的状态。
        返回更新后的表单字典；非测试环境、非法状态或表单不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "submitted", "processing", "completed", "failed"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo form status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET status = %s WHERE id = %s",
            (status, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_form_pipeline_run_for_test(self, form_id: int, pipeline_run_id: str) -> dict[str, Any]:
        """只修改测试表单的 Pipeline Run ID，以隔离运行关联校验场景。

        参数 ``form_id`` 是自动化创建的表单主键，``pipeline_run_id`` 是测试用的非空运行标识。
        返回更新后的表单字典；非测试环境、运行标识为空、长度超限或表单不存在时抛出异常。
        """

        normalized_pipeline_run_id = pipeline_run_id.strip()
        if not 1 <= len(normalized_pipeline_run_id) <= 255:
            raise ValueError("pipeline_run_id must contain 1 to 255 non-whitespace characters")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET pipeline_run_id = %s WHERE id = %s",
            (normalized_pipeline_run_id, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_pool_status_for_test(self, pool_id: int, status: str) -> dict[str, Any]:
        """只修改测试因子池状态以构造锁定状态冲突。

        参数 ``pool_id`` 是自动化创建的因子池主键，``status`` 必须是表结构允许的状态。
        返回更新后的因子池字典；非测试环境、非法状态或因子池不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "locked", "archived"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo pool status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
            (status, pool_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
        pool = self.get_pool(pool_id)
        if pool is None:
            raise RuntimeError(f"Updated factor combo pool cannot be read: {pool_id}")
        return pool

    def claim_feedback_for_worker(self, feedback_id: int) -> dict[str, Any]:
        """将测试创建的 pending Feedback 标记为已被模拟 Worker 认领。

        参数 ``feedback_id`` 是需要认领的反馈主键。
        返回更新后的反馈字典；仅允许测试环境写入，反馈不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        result = self._client.execute(
            """
            UPDATE factor_combo_experiment_feedback
            SET status = %s,
                claimed_at = UTC_TIMESTAMP(3)
            WHERE id = %s
              AND status = %s
            """,
            ("processing", feedback_id, "pending"),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Pending factor combo feedback cannot be claimed: {feedback_id}")
        feedback = self.get_feedback(feedback_id)
        if feedback is None:
            raise RuntimeError(f"Claimed factor combo feedback cannot be read: {feedback_id}")
        return feedback

    def clean_test_graph(self, resource_graph: Mapping[int, Iterable[int]]) -> None:
        """删除由自动化创建且已进入安全终态的组合因子测试数据图。

        参数 ``resource_graph`` 是 ``{session_id: {form_id, ...}}`` 形式的当前测试资源归属图；
        Repository 会在事务中再次核对每个表单的 ``session_id``，不接受跨会话或未能确认归属的删除请求。
        不返回值；异步 Pipeline、刷新任务仍处于活动或未知状态时保留整组业务图。``factor_ic_runs`` 主表不删除，
        因为它没有测试子因子归属字段且可能被多个因子共享；只清理可以按生成子因子唯一定位的明细行。
        """

        self._assert_test_write_allowed()
        normalized_graph: dict[int, set[int]] = {
            int(session_id): {int(form_id) for form_id in form_ids}
            for session_id, form_ids in resource_graph.items()
        }
        form_owner: dict[int, int] = {}
        for session_id, form_ids in normalized_graph.items():
            for form_id in form_ids:
                previous_owner = form_owner.setdefault(form_id, session_id)
                if previous_owner != session_id:
                    raise ValueError(f"Form {form_id} is assigned to multiple test sessions")
        normalized_form_ids = sorted(form_owner)
        normalized_session_ids = sorted(normalized_graph)
        if not normalized_form_ids and not normalized_session_ids:
            return

        with self._client.transaction() as transaction:
            form_rows = self._fetch_forms_for_cleanup(
                transaction,
                normalized_form_ids,
                normalized_session_ids,
            )
            form_rows_by_id = {
                int(row["id"]): row
                for row in form_rows
                if row.get("id") is not None
            }
            for form_id, expected_session_id in form_owner.items():
                row = form_rows_by_id.get(form_id)
                if row is None:
                    # 已被前一次清理删除的资源不阻止同一 Scope 的幂等清理，但不能把缺失 ID 继续拼进 DELETE。
                    continue
                actual_session_id = row.get("session_id")
                if actual_session_id is None or int(actual_session_id) != expected_session_id:
                    raise RuntimeError(
                        f"Refusing to clean form {form_id}: database session does not match test ownership"
                    )

            owned_form_ids = sorted(
                form_id
                for form_id in normalized_form_ids
                if form_id in form_rows_by_id
            )
            owned_form_rows = [form_rows_by_id[form_id] for form_id in owned_form_ids]
            if self._has_active_pipeline_runs(owned_form_rows):
                # 表单仍携带未终态 Pipeline Run 时，不能仅依赖 Service 的内存保护；直接 API/Worker 流程也必须安全。
                return

            version_rows = self._fetch_versions_for_forms(transaction, owned_form_ids)
            if any(
                row.get("id") is None
                or row.get("combo_id") is None
                or not str(row.get("combo_version_hash") or "").strip()
                for row in version_rows
            ):
                # 缺少具体版本、组合或版本哈希时，无法证明后续实验和登记记录属于当前 Scope。
                return
            try:
                version_ids = sorted({int(row["id"]) for row in version_rows})
                business_combo_ids = {int(row["combo_id"]) for row in version_rows}
                pool_ids = {
                    int(row["pool_id"])
                    for row in version_rows
                    if row.get("pool_id") is not None
                }
                pool_ids.update(
                    int(row["factor_combo_pool_id"])
                    for row in owned_form_rows
                    if row.get("factor_combo_pool_id") is not None
                )
            except (TypeError, ValueError):
                # 数据库返回了无法解析的版本主键时，禁止继续构造删除条件。
                return
            combo_version_hashes = [
                str(row["combo_version_hash"]).strip()
                for row in version_rows
                if row.get("combo_version_hash") is not None
            ]
            version_id_set = set(version_ids)
            accepted_combo_identities = version_id_set | business_combo_ids
            combo_version_hash_set = set(combo_version_hashes)
            try:
                experiment_ids = [
                    int(row["experiment_id"])
                    for row in version_rows
                    if row.get("experiment_id") is not None
                ]
                experiment_ids.extend(
                    int(row["best_experiment_result_id"])
                    for row in version_rows
                    if row.get("best_experiment_result_id") is not None
                )
                experiment_ids.extend(
                    int(row["factor_combo_experiment_info_id"])
                    for row in owned_form_rows
                    if row.get("factor_combo_experiment_info_id") is not None
                )
            except (TypeError, ValueError):
                return
            experiment_ids = sorted(set(experiment_ids))
            component_rows = self._fetch_components_for_cleanup(transaction, version_ids)
            experiment_rows = self._fetch_experiments_for_cleanup(transaction, experiment_ids)
            try:
                experiment_rows_by_id = {
                    int(row["id"]): row
                    for row in experiment_rows
                    if row.get("id") is not None
                }
            except (TypeError, ValueError):
                return
            if len(experiment_rows_by_id) != len(experiment_ids):
                # 指针指向的实验已缺失时，无法证明后续删除范围完整，保留整组图等待人工处理。
                return
            for experiment_row in experiment_rows:
                try:
                    experiment_combo_id = experiment_row.get("combo_id")
                    if experiment_combo_id is None or int(experiment_combo_id) not in accepted_combo_identities:
                        # 新版记录使用业务组合 ID；仅对当前版本直接指向的旧实验兼容版本主键，其他值不能进入删除范围。
                        return
                except (TypeError, ValueError):
                    return
            metric_rows = self._fetch_metrics_for_cleanup(transaction, experiment_ids, version_ids)
            if any(
                row.get("id") is None
                or row.get("experiment_info_id") is None
                or row.get("combo_id") is None
                for row in metric_rows
            ):
                # 指标缺少任一归属身份时，无法确认删除范围，避免留下实验的半清理状态。
                return
            try:
                metric_ids = sorted({int(row["id"]) for row in metric_rows})
                for metric_row in metric_rows:
                    if int(metric_row["experiment_info_id"]) not in experiment_rows_by_id:
                        return
                    if int(metric_row["combo_id"]) not in accepted_combo_identities:
                        return
            except (TypeError, ValueError):
                return
            try:
                metric_rows_by_id = {int(row["id"]): row for row in metric_rows}
                metric_ids_by_experiment = {
                    int(row["metrics_id"])
                    for row in experiment_rows
                    if row.get("metrics_id") is not None
                }
            except (TypeError, ValueError):
                return
            if not metric_ids_by_experiment.issubset(set(metric_ids)):
                # 实验已经声明指标指针，但指标记录无法按当前版本/实验找到，保留整组图。
                return
            for experiment_row in experiment_rows:
                metrics_id = experiment_row.get("metrics_id")
                if metrics_id is None:
                    continue
                try:
                    metric_row = metric_rows_by_id[int(metrics_id)]
                    if int(metric_row["experiment_info_id"]) != int(experiment_row["id"]):
                        return
                    if int(metric_row["combo_id"]) != int(experiment_row["combo_id"]):
                        return
                except (KeyError, TypeError, ValueError):
                    return
            registration_rows = self._fetch_registrations(
                transaction,
                version_ids,
                combo_version_hashes,
                business_combo_ids=sorted(business_combo_ids),
            )
            try:
                registration_ids = sorted(
                    {
                        int(row["id"])
                        for row in registration_rows
                        if row.get("id") is not None
                    }
                )
            except (TypeError, ValueError):
                return
            for registration_row in registration_rows:
                try:
                    registration_combo_id = registration_row.get("combo_id")
                    registration_version_id = registration_row.get("version_id")
                    registration_hash = str(registration_row.get("combo_version_hash") or "").strip()
                    if (
                        registration_row.get("id") is None
                        or registration_row.get("sub_factor_id") is None
                        or registration_combo_id is None
                        or registration_version_id is None
                        or int(registration_combo_id) not in accepted_combo_identities
                        or int(registration_version_id) not in version_id_set
                        or registration_hash not in combo_version_hash_set
                    ):
                        # 新版业务组合 ID 或历史版本主键都必须再以版本哈希命中具体版本，不能只按 combo_id 猜测归属。
                        return
                except (TypeError, ValueError):
                    return
            try:
                generated_sub_factor_ids = sorted(
                    {
                        int(row["sub_factor_id"])
                        for row in registration_rows
                        if row.get("sub_factor_id") is not None
                    }
                )
            except (TypeError, ValueError):
                return
            owned_factor_relations, owned_parent_relations = self._expected_lineage_relations(
                version_rows,
                component_rows,
                registration_rows,
            )
            if self._has_external_references(
                transaction,
                generated_sub_factor_ids,
                registration_ids,
                experiment_ids,
                version_ids,
                sorted(pool_ids),
                owned_form_ids,
                metric_ids,
                owned_factor_relations,
                owned_parent_relations,
            ):
                # 任何共享登记、父子关系或实验引用都意味着当前 Scope 不能独占这些实体。
                return
            if self._has_active_refreshes(transaction, generated_sub_factor_ids):
                # 异步刷新尚未进入明确终态时，整组业务图都必须保留，避免删掉 Worker 仍在写入的目标。
                return

            self._delete_in(transaction, "factor_combo_registered_factor", "id", registration_ids)
            self._delete_in(
                transaction,
                "factor_validity_status",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )

            # 这些表的 factor_id/sub_factor_id 指向本次登记生成的子因子，可以按唯一 ID 清理；Run 主表只保留审计记录。
            self._delete_in(
                transaction,
                "factor_ic_slice_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "factor_value_slice_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "factor_ic_summary_metrics",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(
                transaction,
                "sub_factor_refreshes",
                "sub_factor_id",
                generated_sub_factor_ids,
            )

            # 这两类记录引用生成子因子，必须在删除子因子及其详情前解除；否则 ON DELETE RESTRICT 会回滚事务。
            self._delete_in(transaction, "factor_combo_experiment_feedback", "form_id", owned_form_ids)
            self._delete_in(transaction, "factor_combo_component", "combo_id", version_ids)
            self._delete_in(transaction, "factor_combo_pool_member", "factor_combo_form_id", owned_form_ids)

            self._delete_in(
                transaction,
                "factors_details",
                "factor_id",
                generated_sub_factor_ids,
                suffix="AND is_sub_factor_id = 1",
            )
            self._delete_in(transaction, "sub_factor_parent_relations", "sub_factor_id", generated_sub_factor_ids)
            self._delete_in(transaction, "factor_sub_factor_relations", "sub_factor_id", generated_sub_factor_ids)
            self._delete_in(transaction, "sub_factors", "id", generated_sub_factor_ids)
            self._delete_in(transaction, "factor_combo_form", "id", owned_form_ids, update_only=True)
            self._clear_experiment_metric_pointers(transaction, experiment_ids)
            self._clear_combo_experiment_pointers(transaction, version_ids)
            self._delete_in(transaction, "factor_combo_metrics", "id", metric_ids)
            self._delete_in(transaction, "factor_combo_experiment_info", "id", experiment_ids)
            self._delete_in(transaction, "factor_combo", "id", version_ids)
            self._delete_in(transaction, "factor_combo_pool", "factor_combo_form_id", owned_form_ids)
            self._delete_in(transaction, "factor_combo_form", "id", owned_form_ids)
            cleaned_test_parent_factor_ids = self._clean_test_parent_factors(
                transaction,
                normalized_form_ids,
            )

            cleanable_session_ids = self._sessions_without_remaining_forms(
                form_rows,
                owned_form_ids,
                normalized_session_ids,
            )
            self._delete_in(transaction, "chat_messages", "session_id", cleanable_session_ids)
            self._delete_in(transaction, "chat_sessions", "id", cleanable_session_ids)

        for form_id in normalized_form_ids:
            remaining_ids = self._test_parent_factor_ids_by_form.get(form_id, set())
            remaining_ids.difference_update(cleaned_test_parent_factor_ids)
            if remaining_ids:
                self._test_parent_factor_ids_by_form[form_id] = remaining_ids
            else:
                self._test_parent_factor_ids_by_form.pop(form_id, None)

    def _clean_test_parent_factors(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
    ) -> set[int]:
        """删除当前 Scope 创建且已解除全部引用的临时母因子。

        参数 ``transaction`` 是组合图清理事务，``form_ids`` 是本次完成清理的表单集合。
        返回已删除的临时母因子 ID 集合；发现外部引用时保留记录并不返回该 ID，数据库查询或删除失败直接抛出异常。
        """

        candidate_ids = sorted(
            {
                factor_id
                for form_id in form_ids
                for factor_id in self._test_parent_factor_ids_by_form.get(int(form_id), set())
            }
        )
        if not candidate_ids:
            return set()
        cleaned_ids: set[int] = set()
        for factor_id in candidate_ids:
            row = transaction.fetch_one(
                """
                SELECT id, factor_name
                FROM factors
                WHERE id = %s
                  AND LEFT(factor_name, CHAR_LENGTH(%s)) = %s
                FOR UPDATE
                """,
                (factor_id, _TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX),
            )
            if row is None:
                cleaned_ids.add(factor_id)
                continue
            reference = transaction.fetch_one(
                """
                SELECT 1 AS external_reference
                FROM factors_details
                WHERE factor_id = %s
                UNION ALL
                SELECT 1 AS external_reference
                FROM factor_sub_factor_relations
                WHERE factor_id = %s
                UNION ALL
                SELECT 1 AS external_reference
                FROM factor_theme_relations
                WHERE factor_id = %s
                UNION ALL
                SELECT 1 AS external_reference
                FROM factor_combo_component
                WHERE component_factor_id = %s
                UNION ALL
                SELECT 1 AS external_reference
                FROM factor_validity_status
                WHERE factor_id = %s
                  AND is_sub_factor_id = 0
                LIMIT 1
                """,
                (factor_id, factor_id, factor_id, factor_id, factor_id),
            )
            if reference is not None:
                continue
            result = transaction.execute(
                "DELETE FROM factors WHERE id = %s",
                (factor_id,),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Test parent factor could not be deleted: {factor_id}")
            cleaned_ids.add(factor_id)
        return cleaned_ids

    @staticmethod
    def _has_active_pipeline_runs(form_rows: Sequence[Mapping[str, Any]]) -> bool:
        """判断表单上的 Pipeline Run 是否仍处于活动或未知状态。

        参数 ``form_rows`` 是清理事务已读取的表单行。返回 ``True`` 表示至少一条非空
        ``pipeline_run_id`` 没有明确终态；框架生成的 ``legacy-simulated-form-*`` Worker 合约标识不对应异步任务，
        可以直接清理。其他 Run 缺少终态时仍按活动状态处理，确保真实 Agent 流程不会绕过保护。
        """

        terminal_statuses = {
            "completed",
            "complete",
            "success",
            "succeeded",
            "failed",
            "partial",
            "partial_failed",
            "partial_fail",
            "error",
            "cancelled",
            "canceled",
            "aborted",
            "skipped",
            "invalid",
            "rejected",
            "expired",
        }
        for row in form_rows:
            pipeline_run_id = str(row.get("pipeline_run_id") or "").strip()
            if not pipeline_run_id:
                continue
            if pipeline_run_id.startswith(_SIMULATED_PIPELINE_RUN_PREFIX):
                continue
            status = str(row.get("status") or "").strip().lower().replace("-", "_").replace(" ", "_")
            if status not in terminal_statuses:
                return True
        return False

    @staticmethod
    def _sessions_without_remaining_forms(
        form_rows: Sequence[Mapping[str, Any]],
        deleted_form_ids: Sequence[int],
        session_ids: Sequence[int],
    ) -> list[int]:
        """计算删除目标后仍没有其他表单的会话。

        参数 ``form_rows`` 是清理前按目标表单或目标会话读取的所有表单行，``deleted_form_ids`` 是本次实际删除的表单，
        ``session_ids`` 是当前 Scope 登记的会话。返回可以安全删除消息和会话记录的主键列表；仍有未登记表单的会话会被保留。
        """

        deleted = {int(form_id) for form_id in deleted_form_ids}
        remaining_by_session: dict[int, set[int]] = {int(session_id): set() for session_id in session_ids}
        for row in form_rows:
            session_id = row.get("session_id")
            form_id = row.get("id")
            if session_id is None or form_id is None:
                continue
            normalized_session_id = int(session_id)
            if normalized_session_id in remaining_by_session and int(form_id) not in deleted:
                remaining_by_session[normalized_session_id].add(int(form_id))
        return [session_id for session_id, remaining in remaining_by_session.items() if not remaining]

    @staticmethod
    def _expected_lineage_relations(
        version_rows: Sequence[Mapping[str, Any]],
        component_rows: Sequence[Mapping[str, Any]],
        registration_rows: Sequence[Mapping[str, Any]],
    ) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
        """计算当前测试图应当拥有的母因子和子因子父子关系。

        参数 ``version_rows`` 是当前表单产生的组合版本，``component_rows`` 是版本成分，``registration_rows`` 是登记
        映射。返回 ``(factor_sub_factor_relations, sub_factor_parent_relations)`` 两个关系集合；集合中的元组分别是
        ``(factor_id, generated_sub_factor_id)`` 和 ``(parent_sub_factor_id, generated_sub_factor_id)``。关系来源于当前
        版本成分或登记映射本身，不能把这些正常关系误判成外部共享引用。
        """

        version_by_hash = {
            str(row["combo_version_hash"]): int(row["id"])
            for row in version_rows
            if row.get("id") is not None and row.get("combo_version_hash")
        }
        components_by_version: dict[int, list[Mapping[str, Any]]] = {}
        for component in component_rows:
            version_id = component.get("combo_id")
            if version_id is None:
                continue
            components_by_version.setdefault(int(version_id), []).append(component)

        factor_relations: set[tuple[int, int]] = set()
        parent_relations: set[tuple[int, int]] = set()
        for registration in registration_rows:
            sub_factor_id = registration.get("sub_factor_id")
            if sub_factor_id is None:
                continue
            generated_sub_factor_id = int(sub_factor_id)
            version_id = registration.get("version_id")
            if version_id is None:
                version_id = version_by_hash.get(str(registration.get("combo_version_hash") or ""))
            if version_id is None and registration.get("combo_id") is not None:
                candidate = int(registration["combo_id"])
                if candidate in components_by_version:
                    version_id = candidate

            source_components = components_by_version.get(int(version_id), []) if version_id is not None else []
            source_factor_ids = {
                int(component["component_factor_id"])
                for component in source_components
                if component.get("component_factor_id") is not None
            }
            if registration.get("factor_id") is not None:
                source_factor_ids.add(int(registration["factor_id"]))
            factor_relations.update((factor_id, generated_sub_factor_id) for factor_id in source_factor_ids)

            source_sub_factor_ids = {
                int(component["component_sub_factor_id"])
                for component in source_components
                if component.get("component_sub_factor_id") is not None
            }
            parent_relations.update(
                (parent_sub_factor_id, generated_sub_factor_id)
                for parent_sub_factor_id in source_sub_factor_ids
            )
        return factor_relations, parent_relations

    @staticmethod
    def _relation_not_in_predicate(
        first_column: str,
        second_column: str,
        relations: set[tuple[int, int]],
    ) -> str:
        """生成固定关系列不在当前 Scope 关系集合中的 SQL 条件。

        参数 ``first_column`` 和 ``second_column`` 是 Repository 内部固定列名，``relations`` 是需要排除的关系集合。
        返回带 ``%s`` 参数占位符的 SQL 片段；集合为空时返回恒真条件。列名不接受外部输入。
        """

        if not relations:
            return "1 = 1"
        pairs = " OR ".join(
            f"({first_column} = %s AND {second_column} = %s)"
            for _ in sorted(relations)
        )
        return f"NOT ({pairs})"

    @staticmethod
    def _relation_predicate_parameters(relations: set[tuple[int, int]]) -> tuple[int, ...]:
        """按关系谓词生成顺序展开绑定参数。

        参数 ``relations`` 是关系元组集合。返回按字典序排列的扁平整数元组，必须与
        ``_relation_not_in_predicate`` 的占位符顺序一致。
        """

        return tuple(value for relation in sorted(relations) for value in relation)

    def _has_external_references(
        self,
        transaction: DatabaseTransaction,
        sub_factor_ids: Sequence[int],
        registration_ids: Sequence[int],
        experiment_ids: Sequence[int],
        version_ids: Sequence[int],
        pool_ids: Sequence[int],
        form_ids: Sequence[int],
        metric_ids: Sequence[int],
        owned_factor_relations: set[tuple[int, int]],
        owned_parent_relations: set[tuple[int, int]],
    ) -> bool:
        """判断清理目标是否被当前 Scope 之外的业务记录引用。

        参数 ``transaction`` 是当前清理事务；其余集合分别表示本次生成的子因子、登记、实验、组合版本、因子池、
        表单和指标主键，以及当前组合成分产生的正常谱系关系。返回 ``True`` 表示发现任一外部登记、组合、因子池、
        表单、反馈、指标或谱系引用，调用方应保留整组数据；查询或数据库异常直接向上抛出。
        """

        statements: list[tuple[str, tuple[int, ...]]] = []
        normalized_sub_factor_ids = tuple(int(value) for value in sub_factor_ids)
        normalized_registration_ids = tuple(int(value) for value in registration_ids)
        normalized_experiment_ids = tuple(int(value) for value in experiment_ids)
        normalized_version_ids = tuple(int(value) for value in version_ids)
        normalized_pool_ids = tuple(int(value) for value in pool_ids)
        normalized_form_ids = tuple(int(value) for value in form_ids)
        normalized_metric_ids = tuple(int(value) for value in metric_ids)

        if normalized_pool_ids:
            pool_placeholders = self._placeholders(normalized_pool_ids)
            if normalized_version_ids:
                version_placeholders = self._placeholders(normalized_version_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo
                        WHERE pool_id IN ({pool_placeholders})
                          AND id NOT IN ({version_placeholders})
                        """,
                        normalized_pool_ids + normalized_version_ids,
                    )
                )
            else:
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo
                        WHERE pool_id IN ({pool_placeholders})
                        """,
                        normalized_pool_ids,
                    )
                )

            if normalized_form_ids:
                form_placeholders = self._placeholders(normalized_form_ids)
                statements.extend(
                    [
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo_pool_member
                            WHERE pool_id IN ({pool_placeholders})
                              AND (
                                  factor_combo_form_id IS NULL
                                  OR factor_combo_form_id NOT IN ({form_placeholders})
                              )
                            """,
                            normalized_pool_ids + normalized_form_ids,
                        ),
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo_form
                            WHERE factor_combo_pool_id IN ({pool_placeholders})
                              AND id NOT IN ({form_placeholders})
                            """,
                            normalized_pool_ids + normalized_form_ids,
                        ),
                    ]
                )
            else:
                statements.extend(
                    [
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo_pool_member
                            WHERE pool_id IN ({pool_placeholders})
                            """,
                            normalized_pool_ids,
                        ),
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo_form
                            WHERE factor_combo_pool_id IN ({pool_placeholders})
                            """,
                            normalized_pool_ids,
                        ),
                    ]
                )

        if normalized_sub_factor_ids:
            sub_factor_placeholders = self._placeholders(normalized_sub_factor_ids)
            if normalized_registration_ids:
                registration_placeholders = self._placeholders(normalized_registration_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_registered_factor
                        WHERE sub_factor_id IN ({sub_factor_placeholders})
                          AND id NOT IN ({registration_placeholders})
                        """,
                        normalized_sub_factor_ids + normalized_registration_ids,
                    )
                )
            else:
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_registered_factor
                        WHERE sub_factor_id IN ({sub_factor_placeholders})
                        """,
                        normalized_sub_factor_ids,
                    )
                )
            statements.extend(
                [
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_sub_factor_relations
                        WHERE sub_factor_id IN ({sub_factor_placeholders})
                          AND {self._relation_not_in_predicate('factor_id', 'sub_factor_id', owned_factor_relations)}
                        """,
                        normalized_sub_factor_ids + self._relation_predicate_parameters(owned_factor_relations),
                    ),
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM sub_factor_parent_relations
                        WHERE parent_sub_factor_id IN ({sub_factor_placeholders})
                           OR (
                               sub_factor_id IN ({sub_factor_placeholders})
                               AND {self._relation_not_in_predicate('parent_sub_factor_id', 'sub_factor_id', owned_parent_relations)}
                           )
                        """,
                        normalized_sub_factor_ids
                        + normalized_sub_factor_ids
                        + self._relation_predicate_parameters(owned_parent_relations),
                    ),
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_component
                        WHERE component_sub_factor_id IN ({sub_factor_placeholders})
                          AND combo_id NOT IN ({self._placeholders(normalized_version_ids)})
                        """ if normalized_version_ids else
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_component
                        WHERE component_sub_factor_id IN ({sub_factor_placeholders})
                        """,
                        normalized_sub_factor_ids + normalized_version_ids
                        if normalized_version_ids
                        else normalized_sub_factor_ids,
                    ),
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_pool_member
                        WHERE sub_factor_id IN ({sub_factor_placeholders})
                          AND factor_combo_form_id NOT IN ({self._placeholders(normalized_form_ids)})
                        """ if normalized_form_ids else
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_pool_member
                        WHERE sub_factor_id IN ({sub_factor_placeholders})
                        """,
                        normalized_sub_factor_ids + normalized_form_ids
                        if normalized_form_ids
                        else normalized_sub_factor_ids,
                    ),
                ]
            )

        if normalized_experiment_ids:
            experiment_placeholders = self._placeholders(normalized_experiment_ids)
            experiment_parameters = normalized_experiment_ids
            if normalized_version_ids:
                version_placeholders = self._placeholders(normalized_version_ids)
                version_parameters = normalized_version_ids
                statements.extend(
                    [
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo
                            WHERE experiment_id IN ({experiment_placeholders})
                              AND id NOT IN ({version_placeholders})
                            """,
                            experiment_parameters + version_parameters,
                        ),
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo
                            WHERE best_experiment_result_id IN ({experiment_placeholders})
                              AND id NOT IN ({version_placeholders})
                            """,
                            experiment_parameters + version_parameters,
                        ),
                    ]
                )
            else:
                statements.extend(
                    [
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo
                            WHERE experiment_id IN ({experiment_placeholders})
                            """,
                            experiment_parameters,
                        ),
                        (
                            f"""
                            SELECT 1 AS external_reference
                            FROM factor_combo
                            WHERE best_experiment_result_id IN ({experiment_placeholders})
                            """,
                            experiment_parameters,
                        ),
                    ]
                )
            if normalized_form_ids:
                form_placeholders = self._placeholders(normalized_form_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_form
                        WHERE factor_combo_experiment_info_id IN ({experiment_placeholders})
                          AND id NOT IN ({form_placeholders})
                        """,
                        experiment_parameters + normalized_form_ids,
                    )
                )
            else:
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_form
                        WHERE factor_combo_experiment_info_id IN ({experiment_placeholders})
                        """,
                        experiment_parameters,
                    )
                )

        if normalized_version_ids:
            version_placeholders = self._placeholders(normalized_version_ids)
            version_parameters = normalized_version_ids
            if normalized_form_ids:
                form_placeholders = self._placeholders(normalized_form_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_form
                        WHERE factor_combo_id IN ({version_placeholders})
                          AND id NOT IN ({form_placeholders})
                        """,
                        version_parameters + normalized_form_ids,
                    )
                )
                feedback_scope_predicate = f"(form_id IS NULL OR form_id NOT IN ({form_placeholders}))"
                feedback_scope_parameters = normalized_form_ids
            else:
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_form
                        WHERE factor_combo_id IN ({version_placeholders})
                        """,
                        version_parameters,
                    )
                )
                feedback_scope_predicate = "1 = 1"
                feedback_scope_parameters = ()
            statements.append(
                (
                    f"""
                    SELECT 1 AS external_reference
                    FROM factor_combo_experiment_feedback
                    WHERE {feedback_scope_predicate}
                      AND (
                          source_factor_combo_version_id IN ({version_placeholders})
                          OR next_factor_combo_version_id IN ({version_placeholders})
                      )
                    """,
                    feedback_scope_parameters + version_parameters + version_parameters,
                )
            )
            if normalized_experiment_ids:
                experiment_placeholders = self._placeholders(normalized_experiment_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_experiment_feedback
                        WHERE {feedback_scope_predicate}
                          AND next_experiment_info_id IN ({experiment_placeholders})
                        """,
                        feedback_scope_parameters + normalized_experiment_ids,
                    )
                )

        if normalized_metric_ids:
            metric_placeholders = self._placeholders(normalized_metric_ids)
            if normalized_experiment_ids:
                experiment_placeholders = self._placeholders(normalized_experiment_ids)
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_experiment_info
                        WHERE metrics_id IN ({metric_placeholders})
                          AND id NOT IN ({experiment_placeholders})
                        """,
                        normalized_metric_ids + normalized_experiment_ids,
                    )
                )
            else:
                statements.append(
                    (
                        f"""
                        SELECT 1 AS external_reference
                        FROM factor_combo_experiment_info
                        WHERE metrics_id IN ({metric_placeholders})
                        """,
                        normalized_metric_ids,
                    )
                )

        if not statements:
            return False
        row = transaction.fetch_one(
            "\nUNION ALL\n".join(statement for statement, _ in statements) + "\nLIMIT 1",
            tuple(parameter for _, parameters in statements for parameter in parameters),
        )
        return row is not None

    def _has_active_refreshes(
        self,
        transaction: DatabaseTransaction,
        sub_factor_ids: Sequence[int],
    ) -> bool:
        """检查待清理子因子是否仍有未进入明确终态的刷新任务。

        参数 ``transaction`` 是当前清理事务，``sub_factor_ids`` 是本次登记生成的子因子主键集合。返回 ``True`` 表示
        至少存在一个状态为空、未知或仍在运行的 ``sub_factor_refreshes`` 任务；集合为空时返回 ``False``。查询异常
        会继续向上传递，使清理事务回滚，而不是在无法确认安全性时执行删除。
        """

        if not sub_factor_ids:
            return False
        placeholders = self._placeholders(sub_factor_ids)
        terminal_statuses = (
            "completed",
            "complete",
            "success",
            "succeeded",
            "done",
            "failed",
            "partial",
            "error",
            "cancelled",
            "canceled",
            "aborted",
            "skipped",
        )
        status_placeholders = self._placeholders(terminal_statuses)
        row = transaction.fetch_one(
            f"""
            SELECT 1 AS active_refresh
            FROM sub_factor_refreshes
            WHERE sub_factor_id IN ({placeholders})
              AND (
                  status IS NULL
                  OR LOWER(TRIM(status)) NOT IN ({status_placeholders})
              )
            LIMIT 1
            """,
            tuple(sub_factor_ids) + tuple(terminal_statuses),
        )
        return row is not None

    def _fetch_versions_for_forms(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """在清理事务中查询由表单创建的全部组合版本。

        参数 ``transaction`` 是当前数据库事务，``form_ids`` 是表单主键集合。
        返回版本字典列表；集合为空时返回空列表。
        """

        if not form_ids:
            return []
        placeholders = self._placeholders(form_ids)
        return transaction.fetch_all(
            f"""
            SELECT
                id,
                combo_id,
                pool_id,
                experiment_id,
                best_experiment_result_id,
                combo_version_hash
            FROM factor_combo
            WHERE initial_form_id IN ({placeholders})
            """,
            tuple(form_ids),
        )

    def _fetch_components_for_cleanup(
        self,
        transaction: DatabaseTransaction,
        version_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """读取待清理组合版本的成分和谱系来源。

        参数 ``transaction`` 是当前清理事务，``version_ids`` 是 ``factor_combo.id`` 主键集合。
        返回包含版本 ID、来源母因子 ID 和来源子因子 ID 的成分行；集合为空时返回空列表。
        """

        if not version_ids:
            return []
        placeholders = self._placeholders(version_ids)
        return transaction.fetch_all(
            f"""
            SELECT
                id,
                combo_id,
                component_factor_id,
                component_sub_factor_id
            FROM factor_combo_component
            WHERE combo_id IN ({placeholders})
            """,
            tuple(int(version_id) for version_id in version_ids),
        )

    def _fetch_experiments_for_cleanup(
        self,
        transaction: DatabaseTransaction,
        experiment_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """读取待清理实验的关联组合版本和指标指针。

        参数 ``transaction`` 是当前清理事务，``experiment_ids`` 是 ``factor_combo_experiment_info.id`` 主键集合。
        返回实验 ID、业务组合标识和 ``metrics_id``；集合为空时返回空列表。
        """

        if not experiment_ids:
            return []
        placeholders = self._placeholders(experiment_ids)
        return transaction.fetch_all(
            f"""
            SELECT id, combo_id, metrics_id
            FROM factor_combo_experiment_info
            WHERE id IN ({placeholders})
            """,
            tuple(int(experiment_id) for experiment_id in experiment_ids),
        )

    def _fetch_metrics_for_cleanup(
        self,
        transaction: DatabaseTransaction,
        experiment_ids: Sequence[int],
        version_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """读取当前组合图可以唯一定位的实验指标记录。

        参数 ``transaction`` 是当前清理事务，``experiment_ids`` 是目标实验主键集合，``version_ids`` 是目标组合版本
        主键集合。返回指标主键及其两类关联字段；任一集合为空时仍会按另一类关联查询，两个集合都为空时返回空列表。
        """

        conditions: list[str] = []
        parameters: list[int] = []
        if experiment_ids:
            experiment_placeholders = self._placeholders(experiment_ids)
            conditions.append(f"experiment_info_id IN ({experiment_placeholders})")
            parameters.extend(int(experiment_id) for experiment_id in experiment_ids)
        if version_ids:
            version_placeholders = self._placeholders(version_ids)
            conditions.append(f"combo_id IN ({version_placeholders})")
            parameters.extend(int(version_id) for version_id in version_ids)
        if not conditions:
            return []
        return transaction.fetch_all(
            f"""
            SELECT id, experiment_info_id, combo_id
            FROM factor_combo_metrics
            WHERE {' OR '.join(conditions)}
            """,
            tuple(parameters),
        )

    def _fetch_forms_for_cleanup(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
        session_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """在清理事务中查询目标表单及目标会话下的全部表单。

        参数 ``transaction`` 是当前数据库事务，``form_ids`` 是本次清理目标表单主键集合，``session_ids`` 是当前
        Scope 登记的会话主键集合。返回包含归属、Pipeline 状态和实验指针的表单字典列表；两个集合都为空时返回空列表。
        查询目标会话下的其他表单是为了防止清理时误删仍被其他流程使用的会话。
        """

        conditions: list[str] = []
        parameters: list[int] = []
        if form_ids:
            form_placeholders = self._placeholders(form_ids)
            conditions.append(f"id IN ({form_placeholders})")
            parameters.extend(int(form_id) for form_id in form_ids)
        if session_ids:
            session_placeholders = self._placeholders(session_ids)
            conditions.append(f"session_id IN ({session_placeholders})")
            parameters.extend(int(session_id) for session_id in session_ids)
        if not conditions:
            return []
        return transaction.fetch_all(
            f"""
            SELECT
                id,
                session_id,
                status,
                factor_combo_pool_id,
                pipeline_run_id,
                factor_combo_experiment_info_id
            FROM factor_combo_form
            WHERE {' OR '.join(conditions)}
            """,
            tuple(parameters),
        )

    def _fetch_registrations(
        self,
        transaction: DatabaseTransaction,
        version_ids: Sequence[int],
        combo_version_hashes: Sequence[str],
        *,
        business_combo_ids: Sequence[int] = (),
    ) -> list[dict[str, Any]]:
        """在清理事务中查询组合登记产生的子因子。

        参数 ``transaction`` 是当前数据库事务，``version_ids`` 是 ``factor_combo.id`` 主键集合，
        ``combo_version_hashes`` 是当前目标版本的版本哈希集合，``business_combo_ids`` 是新版登记表使用的业务
        组合 ID。返回登记字典列表；任一必需集合为空时返回空列表。
        查询会把“版本主键命中但哈希错误”或“哈希命中但版本主键错误”的异常登记也读出来，交由调用方保守保留，
        避免只按正确哈希过滤后把损坏或跨版本的登记记录遗漏掉。历史版本主键只有在版本哈希精确命中时才可能被
        调用方接受；无法确认具体版本的记录会阻止清理。
        """

        if not version_ids or not combo_version_hashes:
            return []
        version_placeholders = self._placeholders(version_ids)
        candidate_combo_ids = sorted({int(value) for value in version_ids} | {int(value) for value in business_combo_ids})
        candidate_combo_placeholders = self._placeholders(candidate_combo_ids)
        hash_placeholders = self._placeholders(combo_version_hashes)
        return transaction.fetch_all(
            f"""
            SELECT
                registered.id,
                registered.combo_id,
                registered.combo_version_hash,
                registered.factor_id,
                registered.sub_factor_id,
                version.id AS version_id
            FROM factor_combo_registered_factor AS registered
            LEFT JOIN factor_combo AS version
                ON version.combo_version_hash = registered.combo_version_hash
               AND version.id IN ({version_placeholders})
            WHERE registered.combo_id IN ({candidate_combo_placeholders})
               OR registered.combo_version_hash IN ({hash_placeholders})
            """,
            tuple(int(version_id) for version_id in version_ids)
            + tuple(candidate_combo_ids)
            + tuple(combo_version_hashes),
        )

    def _delete_in(
        self,
        transaction: DatabaseTransaction,
        table_name: str,
        column_name: str,
        values: Sequence[int],
        *,
        suffix: str = "",
        update_only: bool = False,
    ) -> None:
        """在清理事务中按主键集合删除或清空表单关联。

        参数 ``transaction`` 是当前数据库事务，``table_name`` 与 ``column_name`` 来自固定内部白名单，``values`` 是绑定值，
        ``suffix`` 是固定的附加过滤条件，``update_only`` 为真时只清空表单组合和实验指针。
        不返回值；值集合为空时不执行 SQL，底层数据库错误继续向上传递。
        """

        if not values:
            return
        placeholders = self._placeholders(values)
        if update_only:
            transaction.execute(
                f"""
                UPDATE {table_name}
                SET factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE {column_name} IN ({placeholders})
                """,
                tuple(values),
            )
            return
        transaction.execute(
            f"DELETE FROM {table_name} WHERE {column_name} IN ({placeholders}) {suffix}",
            tuple(values),
        )

    def _clear_combo_experiment_pointers(
        self,
        transaction: DatabaseTransaction,
        version_ids: Sequence[int],
    ) -> None:
        """在删除测试实验前解除组合版本的实验外键。

        参数 ``transaction`` 是当前清理事务，``version_ids`` 是待删除的具体组合版本主键。
        不返回值；集合为空时不执行 SQL，数据库错误继续向上传递并触发事务回滚。
        """

        if not version_ids:
            return
        placeholders = self._placeholders(version_ids)
        transaction.execute(
            f"""
            UPDATE factor_combo
            SET experiment_id = NULL,
                best_experiment_result_id = NULL
            WHERE id IN ({placeholders})
            """,
            tuple(version_ids),
        )

    def _clear_experiment_metric_pointers(
        self,
        transaction: DatabaseTransaction,
        experiment_ids: Sequence[int],
    ) -> None:
        """在删除组合指标前解除实验信息表的 ``metrics_id`` 指针。

        参数 ``transaction`` 是当前清理事务，``experiment_ids`` 是待删除的实验信息主键集合。
        不返回值；集合为空时不执行 SQL，数据库错误继续向上传递并触发事务回滚。
        """

        if not experiment_ids:
            return
        placeholders = self._placeholders(experiment_ids)
        transaction.execute(
            f"""
            UPDATE factor_combo_experiment_info
            SET metrics_id = NULL
            WHERE id IN ({placeholders})
            """,
            tuple(experiment_ids),
        )

    @staticmethod
    def _placeholders(values: Sequence[Any]) -> str:
        """为固定长度的 MySQL IN 条件生成参数占位符。

        参数 ``values`` 是需要绑定的值集合。
        返回对应数量的 ``%s`` 占位符字符串；空集合时抛出 ``ValueError``，避免生成无效 SQL。
        """

        if not values:
            raise ValueError("Cannot build SQL placeholders for an empty collection")
        return ", ".join("%s" for _ in values)

    @staticmethod
    def _to_sub_factor_choice(row: Mapping[str, Any] | None) -> SubFactorChoice | None:
        """将子因子查询结果转换为 ``SubFactorChoice``。

        参数 ``row`` 是数据库返回的一行或 ``None``。
        返回转换后的选择对象；没有记录时返回 ``None``。
        """

        if row is None:
            return None
        return SubFactorChoice(
            sub_factor_id=int(row["sub_factor_id"]),
            sub_factor_name=str(row["sub_factor_name"]),
            parent_factor_id=int(row["parent_factor_id"]),
            parent_factor_name=str(row["parent_factor_name"]),
        )

    @staticmethod
    def _normalize_json_columns(row: dict[str, Any] | None, *column_names: str) -> dict[str, Any] | None:
        """将 MySQL JSON 字段统一转换为 Python 对象。

        参数 ``row`` 是数据库查询结果，``column_names`` 是需要解析的 JSON 列名。
        返回复制后的字典或 ``None``；JSON 内容无效时保留原值，便于测试直接报告数据库异常数据。
        """

        if row is None:
            return None
        result = dict(row)
        for column_name in column_names:
            value = result.get(column_name)
            if isinstance(value, str):
                try:
                    result[column_name] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return result

    @staticmethod
    def _normalize_database_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """标准化完整实体查询返回的 JSON 列。

        参数 ``row`` 是 ``SELECT table.*`` 或关联查询返回的一行数据库记录。返回复制后的字典，并解析所有明确的
        JSON 配置、快照和指标列；普通文本字段保持原样。解析失败时保留原始字符串，交由上层对账报告数据库脏数据，
        不会把异常转换成空对象或静默忽略。
        """

        if row is None:
            return None
        json_columns = {
            column_name
            for column_name in row
            if column_name.endswith("_json")
            or column_name in {"metadata", "universe_symbols", "config_json"}
        }
        return FactorComboRepository._normalize_json_columns(row, *sorted(json_columns))

    def _assert_test_write_allowed(self) -> None:
        """阻止在非测试环境执行组合因子测试数据写入。

        不接收参数。
        不返回值；当前环境不是 ``test`` 时抛出 ``RuntimeError``，避免误删或伪造非测试环境业务数据。
        """

        if self._environment != "test":
            raise RuntimeError(
                f"Factor combo database writes are allowed only in the test environment, current: {self._environment!r}"
            )
