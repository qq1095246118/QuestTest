"""组合因子自动化资源图的事务清理。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from db.client import DatabaseTransaction


_TEST_PARENT_FACTOR_PREFIX = "__questtest_unrelated_parent__"
_SIMULATED_PIPELINE_RUN_PREFIX = "legacy-simulated-form-"


class FactorComboCleanupMixin:
    """提供测试环境组合因子资源图的归属校验和事务清理。

    宿主 Repository 提供数据库客户端、测试环境标识、临时母因子归属图及通用占位符方法；本类只负责清理，
    对活动异步任务、未知归属或外部引用一律保守保留。
    """

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
                [*normalized_form_ids, *normalized_session_ids],
            )

            cleanable_session_ids = self._sessions_without_remaining_forms(
                form_rows,
                owned_form_ids,
                normalized_session_ids,
            )
            self._delete_in(transaction, "chat_messages", "session_id", cleanable_session_ids)
            self._delete_in(transaction, "chat_sessions", "id", cleanable_session_ids)

        owner_ids = [*normalized_form_ids, *normalized_session_ids]
        for owner_id in owner_ids:
            remaining_ids = self._test_parent_factor_ids_by_form.get(owner_id, set())
            remaining_ids.difference_update(cleaned_test_parent_factor_ids)
            if remaining_ids:
                self._test_parent_factor_ids_by_form[owner_id] = remaining_ids
            else:
                self._test_parent_factor_ids_by_form.pop(owner_id, None)
            relation_pairs = self._test_parent_relation_pairs_by_owner.get(owner_id, set())
            relation_pairs = {
                pair for pair in relation_pairs if pair[0] not in cleaned_test_parent_factor_ids
            }
            if relation_pairs:
                self._test_parent_relation_pairs_by_owner[owner_id] = relation_pairs
            else:
                self._test_parent_relation_pairs_by_owner.pop(owner_id, None)

    def _clean_test_parent_factors(
        self,
        transaction: DatabaseTransaction,
        form_ids: Sequence[int],
    ) -> set[int]:
        """删除当前 Scope 创建且已解除全部引用的临时母因子。

        参数 ``transaction`` 是组合图清理事务，``form_ids`` 是本次完成清理的表单或会话归属 ID 集合。
        返回已删除的临时母因子 ID 集合；发现外部引用时保留记录并不返回该 ID，数据库查询或删除失败直接抛出异常。
        """

        candidate_ids = sorted(
            {
                factor_id
                for owner_id in form_ids
                for factor_id in self._test_parent_factor_ids_by_form.get(int(owner_id), set())
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
            owned_sub_factor_ids = sorted(
                {
                    sub_factor_id
                    for relation_pairs in self._test_parent_relation_pairs_by_owner.values()
                    for parent_id, sub_factor_id in relation_pairs
                    if parent_id == factor_id
                }
            )
            relation_exclusion = ""
            relation_parameters: tuple[int, ...] = ()
            if owned_sub_factor_ids:
                placeholders = self._placeholders(owned_sub_factor_ids)
                relation_exclusion = f" AND sub_factor_id NOT IN ({placeholders})"
                relation_parameters = tuple(owned_sub_factor_ids)
            reference = transaction.fetch_one(
                f"""
                SELECT 1 AS external_reference
                FROM factors_details
                WHERE factor_id = %s
                UNION ALL
                SELECT 1 AS external_reference
                FROM factor_sub_factor_relations
                WHERE factor_id = %s
                {relation_exclusion}
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
                (factor_id, factor_id, *relation_parameters, factor_id, factor_id, factor_id),
            )
            if reference is not None:
                continue
            for relation_pairs in self._test_parent_relation_pairs_by_owner.values():
                for parent_id, sub_factor_id in sorted(relation_pairs):
                    if parent_id != factor_id:
                        continue
                    transaction.execute(
                        """
                        DELETE FROM factor_sub_factor_relations
                        WHERE factor_id = %s AND sub_factor_id = %s
                        """,
                        (parent_id, sub_factor_id),
                    )
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
        返回初始版本、表单当前版本以及该表单反馈链路 source/next 指向的全部版本；集合为空时返回空列表。
        """

        if not form_ids:
            return []
        placeholders = self._placeholders(form_ids)
        parameters = tuple(form_ids)
        return transaction.fetch_all(
            f"""
            SELECT
                version.id,
                version.combo_id,
                version.pool_id,
                version.experiment_id,
                version.best_experiment_result_id,
                version.combo_version_hash
            FROM factor_combo AS version
            WHERE version.initial_form_id IN ({placeholders})
               OR version.id IN (
                    SELECT form.factor_combo_id
                    FROM factor_combo_form AS form
                    WHERE form.id IN ({placeholders})
                      AND form.factor_combo_id IS NOT NULL
                    UNION
                    SELECT feedback.source_factor_combo_version_id
                    FROM factor_combo_experiment_feedback AS feedback
                    WHERE feedback.form_id IN ({placeholders})
                      AND feedback.source_factor_combo_version_id IS NOT NULL
                    UNION
                    SELECT feedback.next_factor_combo_version_id
                    FROM factor_combo_experiment_feedback AS feedback
                    WHERE feedback.form_id IN ({placeholders})
                      AND feedback.next_factor_combo_version_id IS NOT NULL
               )
            """,
            parameters * 4,
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
    ) -> list[dict[str, Any]]:
        """在清理事务中查询组合登记产生的子因子。

        参数 ``transaction`` 是当前数据库事务，``version_ids`` 是 ``factor_combo.id`` 主键集合，
        ``combo_version_hashes`` 是当前目标版本的版本哈希集合。返回登记字典列表；任一必需集合为空时返回空列表。
        查询以版本哈希作为确定身份，避免历史业务 ``combo_id`` 与当前版本主键发生纯数字碰撞；哈希命中但
        ``combo_id`` 错误的记录仍会返回并由调用方保守阻止清理。
        """

        if not version_ids or not combo_version_hashes:
            return []
        version_placeholders = self._placeholders(version_ids)
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
            WHERE registered.combo_version_hash IN ({hash_placeholders})
            """,
            tuple(int(version_id) for version_id in version_ids)
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
