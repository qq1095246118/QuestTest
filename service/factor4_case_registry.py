"""Factor 4.0 历史业务用例总登记表。

该登记表覆盖规约中的 107 个业务编号。它只负责统计和标记落地状态，不能把
登记、离线单测或数据阻断误判为真实业务通过。
原始来源与需求编号用于历史追溯，不代表当前默认结果级执行范围；执行选择由
Case 专项标记及 pytest 的独立开关控制，合并入口不会删除原迁移登记。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


CoverageState = Literal[
    "IMPLEMENTED_AND_EXECUTED",
    "PARTIALLY_IMPLEMENTED",
    "BLOCKED_DATA_OR_FIXTURE",
    "NOT_IMPLEMENTED",
    "DEFERRED_SCOPE",
]


@dataclass(frozen=True)
class Factor4CaseSpec:
    """描述一个历史 Factor 4.0 业务用例及其当前落地状态。"""

    case_id: str
    module: str
    state: CoverageState
    note: str


@dataclass(frozen=True)
class Factor4ScriptMigration:
    """脚本业务迁移证据；ASSERTED 表示代码已替代，不表示 live 通过。"""

    script_name: str
    case_module: str
    source_case_ids: tuple[str, ...]
    status: Literal["ASSERTED", "PARTIAL", "REMAINING", "NON_CASE_TOOL"]
    note: str
    test_nodes: tuple[str, ...] = ()
    source_removed: bool = False
    excluded_branches: tuple[str, ...] = ()


_SUMMARY_MODULE = "tests/cases/factor4/test_summary_business.py"
_READ_MODULE = "tests/cases/factor4/test_migrated_readonly_scripts.py"
_CALC_MODULE = "tests/cases/factor4/test_calculation_logic.py"
_CATALOG_MODULE = "tests/cases/factor4/test_catalog_extension_business.py"
_AUX_MODULE = "tests/cases/factor4/test_auxiliary_business.py"
_PROTOCOL_DEFERRED = "tests/cases/factor4/test_protocol_deferred_business.py"
_RESEARCH_MODULE = "tests/cases/factor4/test_research_search_business.py"
_CATALOG_NODES = (
    "test_catalog_matching_filters_keep_seed_and_exclude_nonmatching_rows",
    "test_catalog_each_conflicting_filter_is_not_silently_ignored",
    "test_catalog_every_kind_and_status_returns_database_members",
    "test_catalog_valid_subfactor_each_coin_category_matches_database",
    "test_catalog_chinese_name_query_finds_exact_database_seed",
    "test_detail_levels_preserve_database_identity_and_approved_projection",
    "test_detail_batch_preserves_mixed_kinds_input_order_and_duplicates",
    "test_detail_batch_accepts_fifty_valid_refs_without_loss",
    "test_same_numeric_id_remains_separate_factor_and_subfactor_identity",
    "test_parent_children_pagination_exhausts_exact_database_relations",
    _READ_MODULE + "::test_catalog_pagination_and_database_membership",
    _READ_MODULE + "::test_catalog_updated_after_boundary",
    _SUMMARY_MODULE + "::test_formula_evidence_visibility_at_run_completion",
    _RESEARCH_MODULE + "::test_research_search_pages_and_stats_match_latest_db_candidates",
    _RESEARCH_MODULE + "::test_research_metric_thresholds_filter_exact_candidates_and_stats",
    _RESEARCH_MODULE + "::test_research_search_completion_boundary_uses_historical_latest_candidates",
    _RESEARCH_MODULE + "::test_research_explicit_validity_statistics_include_unknown_catalog_entities",
    _RESEARCH_MODULE + "::test_overall_research_search_accepts_either_single_valid_dimension",
    _AUX_MODULE + "::test_kb_exact_candidate_fields_match_database",
    _AUX_MODULE + "::test_kb_all_filters_and_selectors_intersect",
    _AUX_MODULE + "::test_kb_current_task_state_and_lease_projection_match_database",
    _AUX_MODULE + "::test_universe_membership_order_and_fields_match_point_in_time_database",
    _AUX_MODULE + "::test_main_and_altcoin_form_exact_current_all_universe_partition",
    _PROTOCOL_DEFERRED + "::test_catalog_cursor_scope_binding_and_tamper_rejection",
    _PROTOCOL_DEFERRED + "::test_mixed_batch_keeps_existing_positions_and_isolates_missing_ref",
    _PROTOCOL_DEFERRED + "::test_protocol_schema_legal_and_invalid_boundaries",
    _PROTOCOL_DEFERRED + "::test_semantically_invalid_read_arguments_return_explicit_errors",
    _PROTOCOL_DEFERRED + "::test_unknown_kb_candidate_returns_successful_empty_result",
)

_CROSS_SCHEMA_NODES = (
    "tests/cases/factor4/test_schema_business.py::test_approved_schema_complete_members_and_fields_match_database",
    "tests/cases/factor4/test_schema_business.py::test_selected_schema_fields_dependencies_and_replay_are_exact",
    "tests/cases/factor4/test_schema_business.py::test_schema_invalid_selectors_never_fall_back_to_approved_data",
)
_CROSS_PROTOCOL_NODES = (
    "tests/cases/factor4/test_protocol_read_business.py::test_protocol_handshake_notification_and_read_capability",
    "tests/cases/factor4/test_protocol_read_business.py::test_protocol_tool_inventory_has_unique_names_and_object_schemas",
    "tests/cases/factor4/test_protocol_read_business.py::test_protocol_schema_serial_replay_keeps_data_and_request_correlation",
    "tests/cases/factor4/test_protocol_read_business.py::test_protocol_new_connection_reinitializes_same_readonly_surface",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_raw_jsonrpc_rejects_exact_protocol_error",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_semantically_invalid_read_arguments_return_explicit_errors",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_tool_text_json_and_structured_content_are_complete_and_identical",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_missing_invalid_and_malformed_bearer_never_access_read_tools",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_protocol_responses_never_echo_configured_authentication_secret",
)
_CROSS_CATALOG_NODES = (
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_catalog_pagination_and_database_membership",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_catalog_stats_group_counts_are_internally_consistent",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_catalog_repeat_read_is_stable",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_catalog_updated_after_boundary",
    "tests/cases/factor4/test_catalog_extension_business.py::test_catalog_matching_filters_keep_seed_and_exclude_nonmatching_rows",
    "tests/cases/factor4/test_catalog_extension_business.py::test_catalog_conflicting_filter_intersection_returns_successful_empty_page",
    "tests/cases/factor4/test_catalog_extension_business.py::test_catalog_chinese_name_query_finds_exact_database_seed",
    "tests/cases/factor4/test_catalog_extension_business.py::test_detail_levels_preserve_database_identity_and_approved_projection",
    "tests/cases/factor4/test_catalog_extension_business.py::test_detail_batch_preserves_mixed_kinds_input_order_and_duplicates",
    "tests/cases/factor4/test_protocol_deferred_business.py::test_mixed_batch_keeps_existing_positions_and_isolates_missing_ref",
)
_CROSS_RESULT_NODES = (
    "tests/cases/factor4/test_summary_business.py::test_summary_final_fields_single_explicit_and_batch",
    "tests/cases/factor4/test_summary_business.py::test_parent_child_aggregate_summary_matches_exact_database_fields",
    "tests/cases/factor4/test_summary_business.py::test_metric_scopes_match_distinct_factor_union_and_page_limit",
    "tests/cases/factor4/test_summary_business.py::test_metric_scope_completion_is_inclusive_point_in_time",
    "tests/cases/factor4/test_summary_business.py::test_formula_evidence_visibility_at_run_completion",
    "tests/cases/factor4/test_summary_business.py::test_factor_rank_values_order_identity_and_latest_run",
    "tests/cases/factor4/test_summary_business.py::test_three_concurrent_exact_metric_requests_keep_same_database_identity",
    "tests/cases/factor4/test_research_search_business.py::test_research_search_pages_and_stats_match_latest_db_candidates",
    "tests/cases/factor4/test_research_search_business.py::test_research_search_completion_boundary_uses_historical_latest_candidates",
    "tests/cases/factor4/test_research_search_business.py::test_research_explicit_validity_statistics_include_unknown_catalog_entities",
    "tests/cases/factor4/test_research_search_business.py::test_overall_research_search_accepts_either_single_valid_dimension",
    "tests/cases/factor4/test_research_search_business.py::test_mixed_metric_batch_preserves_two_existing_factor_identities_and_local_error",
    "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_same_partition_metrics_and_validity_select_expected_run",
    "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_three_exact_slice_scopes_reconcile_all_pages",
    "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_slice_cursor_is_bound_to_query_and_signature",
    "tests/cases/factor4/test_validity_migration_business.py::test_each_validity_shape_preserves_exact_ts_cs_evidence",
    "tests/cases/factor4/test_validity_migration_business.py::test_existing_and_absent_factor_batch_keep_item_identity_and_error_isolation",
    "tests/cases/factor4/test_validity_migration_business.py::test_metric_and_validity_completion_point_in_time_boundary",
    "tests/cases/factor4/test_validity_migration_business.py::test_invalid_query_and_nullable_symbol_do_not_expose_unrequested_evidence",
    "tests/cases/factor4/test_formula_catalog_business.py::test_all_active_formula_projections_and_approved_inputs",
    "tests/cases/factor4/test_formula_catalog_business.py::test_all_formula_evidence_source_fields_intervals_and_warnings",
    "tests/cases/factor4/test_formula_catalog_business.py::test_formula_catalog_and_evidence_internal_semantics",
    "tests/cases/factor4/test_formula_catalog_business.py::test_catalog_semantic_candidates_are_conditionally_adjudicated",
    "tests/cases/factor4/test_formula_catalog_business.py::test_formula_summary_validity_chain_for_actual_completed_run",
    "tests/cases/factor4/test_cross_read_business.py::test_exact_completed_formula_three_uncached_reads_match_database",
)
_CROSS_ENV_NODES = (
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_daily_is_point_in_time_and_db_consistent",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_date_filter_is_exact",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_current_pointer_and_revision_are_unique",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_revision_three_point_boundary",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_filter_and_final_fields",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_label_filter_is_exact",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_batch_omission_does_not_mix_publications",
    "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_tags_match_active_routes",
    "tests/cases/factor4/test_recommendation_business.py::test_recommendation_current_routes_values_order_and_replay",
    "tests/cases/factor4/test_recommendation_business.py::test_recommendation_publication_visibility_boundary",
    "tests/cases/factor4/test_recommendation_business.py::test_ready_forecast_has_six_normalized_probabilities",
    "tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity",
    "tests/cases/factor4/test_cross_read_business.py::test_daily_first_availability_hides_future_and_includes_equal_boundary",
    "tests/cases/factor4/test_cross_read_business.py::test_recommendations_at_forecast_boundary_keep_visible_publication_and_label",
    "tests/cases/factor4/test_cross_read_business.py::test_environment_unknown_selectors_do_not_substitute_active_data",
)
_CROSS_AUXILIARY_NODES = (
    "tests/cases/factor4/test_auxiliary_business.py::test_kb_exact_candidate_fields_match_database",
    "tests/cases/factor4/test_auxiliary_business.py::test_universe_membership_order_and_fields_match_point_in_time_database",
    "tests/cases/factor4/test_auxiliary_business.py::test_main_and_altcoin_form_exact_current_all_universe_partition",
    "tests/cases/factor4/test_feedback_status_business.py::test_feedback_current_request_maps_to_active_key_owner_and_granted_scope",
    "tests/cases/factor4/test_feedback_status_business.py::test_feedback_owned_submission_status_counters_and_error_match_database",
    "tests/cases/factor4/test_feedback_status_business.py::test_feedback_missing_and_other_owner_ids_do_not_reveal_submission_data",
    "tests/cases/factor4/test_feedback_status_business.py::test_feedback_invalid_or_nonexistent_identifiers_are_rejected_without_leak",
)

_SCRIPT_MIGRATIONS: tuple[Factor4ScriptMigration, ...] = (
    Factor4ScriptMigration(
        "readonly_invariant_probe.py", "tests/cases/factor4/test_lifecycle_final_state.py",
        ("MCP-001", "MCP-002", "MCP-006", "MCP-011", "MCP-014", "MCP-015", "MCP-018", "MCP-019",
         "DB-601", "DB-602", "DB-603", "DB-604", "DB-605", "DB-606", "DB-609", "DB-613"), "ASSERTED",
        "协议/游标/范围矩阵、daily历史和未来可见性、5实体schema、metric唯一及批次引用、全历史route、审计/终态计数与三方对账；异常兼容并发及凭据形态扫描默认暂缓",
        ("test_factor4_entity_schema_preserves_identity_and_revision_uniqueness",
         "test_all_final_metric_units_are_unique_and_reference_existing_batches",
         "test_route_history_retains_identity_and_deactivates_old_versions",
         "test_lifecycle_audit_correlation_actor_time_and_versions",
         "test_batch_terminal_timestamps_and_metric_counts_are_consistent",
         "test_payload_columns_do_not_expose_complete_mcp_token_shapes",
         "tests/cases/factor4/test_protocol_read_business.py::test_protocol_handshake_notification_and_read_capability",
         "tests/cases/factor4/test_protocol_read_business.py::test_protocol_tool_inventory_has_unique_names_and_object_schemas",
         "tests/cases/factor4/test_protocol_read_business.py::test_protocol_schema_serial_replay_keeps_data_and_request_correlation",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_raw_jsonrpc_rejects_exact_protocol_error",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_protocol_schema_legal_and_invalid_boundaries",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_protocol_json_and_sse_accept_negotiation",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_fixed_snapshot_parallel_reads_return_identical_business_data",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_catalog_cursor_scope_binding_and_tamper_rejection",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_unknown_protocol_version_is_rejected_or_explicitly_negotiated",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_daily_is_point_in_time_and_db_consistent",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_daily_visibility_extremes_match_persisted_revisions",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_current_pointer_and_revision_are_unique",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_revision_three_point_boundary",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_filter_and_final_fields",
         "tests/cases/factor4/test_backend_three_way_business.py::test_backend_summary_identity_values_and_period_instants_match_mcp_database",
         "tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity"),
        source_removed=True,
        excluded_branches=("旧route按环境分组把多个不同因子误判重复，改为完整因子/版本/profile唯一性",
                           "DB grants角色未声明、全表水位和宽泛password关键字命中均仅诊断，不能据此证明请求写库或凭据泄漏",
                           "旧DB-607/610/612固定BLOCKED未执行业务，不复制为占位Case；受控事务/清理/Scheduler仍不计迁移业务通过",
                           "未来as-of以实际DB可见修订核验，不强行以执行机今天日期替代可见性契约"),
    ),
    Factor4ScriptMigration(
        "lifecycle_readonly_resume.py", "tests/cases/factor4/test_lifecycle_final_state.py",
        ("LIFE-400", "DB-604", "CALC-511", "CALC-512", "DB-606"), "ASSERTED",
        "发布模式动态契约门禁、全部历史路由/版本、冻结母子成员、同ID终态重读与每阶段审计字段；缺历史/母因子样本明确阻断",
        ("test_publication_mode_is_defined_before_atomicity_acceptance",
         "test_route_history_retains_identity_and_deactivates_old_versions",
         "test_terminal_metrics_use_frozen_factor_membership",
         "test_lifecycle_audit_correlation_actor_time_and_versions",
         "test_terminal_same_batch_results_are_stable_across_reads"), source_removed=True,
        excluded_branches=("旧脚本仅盘点当前关系数量和审计表名称，非可裁决业务断言；未作为覆盖计数",),
    ),
    Factor4ScriptMigration(
        "ranking_parent_snapshot_closure.py", "tests/cases/factor4/test_lifecycle_final_state.py",
        ("CALC-507", "CALC-511"), "ASSERTED",
        "全分区rank/重复读取由正式最终结果Case核验，定义/可执行版本分离与冻结母子关系由生命周期Case核验",
        ("test_terminal_metrics_use_frozen_factor_membership",
         "tests/cases/factor4/test_final_results.py::test_final_result_ranking_is_partitioned_and_repeatable"),
        source_removed=True,
        excluded_branches=("当前可变关系与历史冻结关系差集仅作盘点；差异不构成错误",),
    ),
    Factor4ScriptMigration(
        "temporal_oracle_closure.py", "tests/cases/factor4/test_temporal_final_evidence.py",
        ("CALC-502", "CALC-503"), "ASSERTED",
        "六环境日期/连续区间/冻结revision/PIT/样本天数、OOS顺序与方向冻结；跨工具最终环境指标由只读Case对账",
        ("test_frozen_environment_dates_segments_and_metric_sample_counts",
         "test_final_oos_periods_and_direction_are_not_after_batch_asof",
         "tests/cases/factor4/test_lifecycle_final_state.py::test_terminal_metrics_use_frozen_factor_membership",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_filter_and_final_fields"),
        source_removed=True,
        excluded_branches=("用户已限定最终结果验证；原始bar/持仓/训练输入重放、未来数据扰动不在当前迁移覆盖声明内",
                           "旧脚本硬编码原始输入缺失并固定BLOCKED的分支不复制为业务用例"),
    ),
    *(
        Factor4ScriptMigration(name, _CATALOG_MODULE, ("MCP-005", "MCP-006", "MCP-018"), "ASSERTED",
            "目录十状态/四分类、交集/空页、中文查询、详情层级/混合批量/全部children、独立research TS/CS搜索/阈值/PIT、KB/Universe已真实断言；异常游标/schema/不存在项完整实现为deferred，代码完成不等于live通过",
            _CATALOG_NODES, source_removed=True,
            excluded_branches=("旧stats把完成指标distinct factor_ref数与library实体数直接比较口径错误；新Case分别校验独立口径",
                               "历史报告修订/计时诊断不计业务断言；无因果全库前后水位不作为MCP只读证明"))
        for name in ("catalog_deep_readonly.py", "catalog_deep_expansion.py", "catalog_deep_recheck.py")
    ),
    Factor4ScriptMigration("catalog_kb_remaining_probe.py", _AUX_MODULE, ("MCP-006", "MCP-019"), "ASSERTED",
        "最小目录/KB精确查、TS scope发现与独立TS factor_search均由实际业务/DB断言替代，不拿factor_rank冒充",
        ("test_kb_exact_candidate_fields_match_database", _CATALOG_MODULE + "::test_catalog_all_total_equals_factor_and_subfactor_totals",
         _SUMMARY_MODULE + "::test_metric_scopes_match_distinct_factor_union_and_page_limit",
         _RESEARCH_MODULE + "::test_research_search_pages_and_stats_match_latest_db_candidates"), source_removed=True),
    Factor4ScriptMigration("filter_error_kb_deep.py", _CATALOG_MODULE, ("MCP-005", "MCP-006", "MCP-018"), "ASSERTED",
        "筛选/分页/批量/状态/KB全部真实DB断言；非法类型/游标篡改/不存在混合项已deferred真实实现",
        (*_CATALOG_NODES, _AUX_MODULE + "::test_kb_mapped_candidate_resolves_same_typed_catalog_entity"), source_removed=True,
        excluded_branches=("全库前后水位没有请求因果证明，不复制该错误只读guard",)),
    *(
        Factor4ScriptMigration(name, "", (), "NON_CASE_TOOL", note)
        for name, note in (
            ("filter_error_kb_correct.py", "仅加载固定历史报告并修正 verdict/已排除兼容项，无实时接口/DB 测试入口；保留报告修订工具，不计业务 Case"),
            ("field_runtime_mcp.py", "仅读取历史 unresolved 名单，采集当前 batch detail/raw schema 并输出字段风险证据，无可判定业务预期；保留诊断工具，schema/详情读取一致性已由正式 Case 覆盖"),
            ("field_runtime_db.py", "仅基于历史名单统计 runtime 产出，固定 static_contract_risk_only 和 confirmed_failure_count=0；不能把已有非空历史指标当当前字段正确性断言，保留诊断工具"),
        )
    ),
    Factor4ScriptMigration("kb_topup_route_audit.py", _AUX_MODULE, ("CALC-510", "DB-613"), "ASSERTED",
        "KB映射/任务result身份、route摘要计数、MCP tags/metric/publication链全部正式断言；不照搬固定FAIL候选文案",
        ("test_kb_mapped_candidate_resolves_same_typed_catalog_entity", "test_kb_current_task_state_and_lease_projection_match_database",
         "tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity",
         _READ_MODULE + "::test_environment_tags_match_active_routes", _READ_MODULE + "::test_environment_metric_filter_and_final_fields"), source_removed=True,
        excluded_branches=("Topup同父同window字符串差异/KB文本语义候选无明确等价契约，仅风险诊断，不计确认缺陷或通过",
                           "quota gate只记录自然EXPORT_BUDGET_EXCEEDED，没有预设准入条件/阈值，不伪造限流业务Case")),
    Factor4ScriptMigration(
        "recommendation_pit_recheck.py", "tests/cases/factor4/test_recommendation_business.py",
        ("REC-210",), "ASSERTED",
        "全部发布分区的前/等/后微秒 PIT：DB 历史版本、发布时间、无发布原因及预测无未来泄漏；真实 MCP 验证通过",
        ("test_recommendation_publication_visibility_boundary",), source_removed=True,
    ),
    *(
        Factor4ScriptMigration(name, "tests/cases/factor4/test_protocol_read_business.py", ("MCP-001", "MCP-002", "MCP-006", "MCP-010", "MCP-011", "MCP-014", "MCP-015"), "ASSERTED",
            "正常握手/通知/目录/串行ID/重连已业务断言；read工具schema矩阵、严格双表示、畸形RPC精确错误码、SSE、session、未知版本、最大日线分页及独立连接并发已实现deferred并默认不执行",
            ("test_protocol_handshake_notification_and_read_capability", "test_protocol_tool_inventory_has_unique_names_and_object_schemas", "test_protocol_schema_serial_replay_keeps_data_and_request_correlation",
             _PROTOCOL_DEFERRED + "::test_raw_jsonrpc_rejects_exact_protocol_error",
             _PROTOCOL_DEFERRED + "::test_protocol_schema_legal_and_invalid_boundaries",
             _PROTOCOL_DEFERRED + "::test_protocol_json_and_sse_accept_negotiation",
             _PROTOCOL_DEFERRED + "::test_unknown_protocol_version_is_rejected_or_explicitly_negotiated",
             _PROTOCOL_DEFERRED + "::test_fixed_snapshot_parallel_reads_return_identical_business_data",
             _PROTOCOL_DEFERRED + "::test_stateful_session_id_cannot_be_omitted_or_forged",
             _PROTOCOL_DEFERRED + "::test_tool_text_json_and_structured_content_are_complete_and_identical",
             _PROTOCOL_DEFERRED + "::test_large_daily_pages_exhaust_exact_database_rows_without_truncation",
             _PROTOCOL_DEFERRED + "::test_feedback_write_tool_schema_is_inspected_without_invocation",
             "test_protocol_new_connection_reinitializes_same_readonly_surface",
             "tests/cases/factor4/test_feedback_status_business.py::test_feedback_invalid_or_nonexistent_identifiers_are_rejected_without_leak"), source_removed=True,
            excluded_branches=("服务端畸形SSE注入原为NOT_APPLICABLE，客户端无法强制远端产生；本地解析反例仅unit，不计live",
                               "全表水位相同不证明请求无副作用；stateless服务无Session时明确不可适用",
                               "旧伪造不存在ref/run作为合法baseline已删除，新Case必须从真实DB发现前置"))
        for name in ("protocol_boundary_probe.py", "protocol_gap_closure.py")
    ),
    Factor4ScriptMigration(
        "auto_run_selection_recheck.py", "tests/cases/factor4/test_run_selection_and_slice_scopes.py",
        ("MCP-016", "MCP-019"), "ASSERTED",
        "同因子同完整scope双Run动态发现；TS/CS metrics+validity自动最新/显式旧Run及省略Run的slice联动均有独立DB业务断言",
        ("test_same_partition_metrics_and_validity_select_expected_run",
         "test_omitted_run_slices_belong_to_latest_same_partition_run"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "resume_slice_scopes_probe.py", "tests/cases/factor4/test_run_selection_and_slice_scopes.py",
        ("MCP-016",), "ASSERTED",
        "三种slice scope独立发现，精确symbol/Run/版本/resolved_scope、七行分页、成员/顺序/重复/全部字段与局部水位；跨scope/limit/tamper cursor有真实暂缓Case",
        ("test_three_exact_slice_scopes_reconcile_all_pages", "test_slice_cursor_cannot_cross_related_scopes",
         "test_slice_cursor_is_bound_to_query_and_signature"), source_removed=True,
        excluded_branches=("共享测试库水位变化不能证明MCP写入；改为SNAPSHOT_DRIFT明确阻断，未把因果不成立的只读写入判断计覆盖",),
    ),
    Factor4ScriptMigration(
        "rank_targeted_current.py", _SUMMARY_MODULE, ("MCP-016",), "ASSERTED",
        "TS 币种/CS 独立 factor_rank：DB 最新主键/Run、数值、方向乘法、顺序、数量与去重；缺样本阻断",
        ("test_factor_rank_values_order_identity_and_latest_run",),
        source_removed=True,
    ),
    Factor4ScriptMigration(
        "rank_targeted_recheck.py", _SUMMARY_MODULE, ("MCP-016",), "ASSERTED",
        "零大小两次重放和公式完成时间前/等/后边界；旧 report ID 已改为动态 DB 证据",
        ("test_factor_rank_requested_sides_and_repeat_are_stable",
         "test_formula_evidence_visibility_at_run_completion"),
        source_removed=True,
    ),
    Factor4ScriptMigration(
        "metric_scope_visibility_probe.py", _SUMMARY_MODULE, ("MCP-019",), "ASSERTED",
        "当前 scope 因子并集/周期/完成时点、limit=1 与 PIT 三点；截断未观察目标不算通过",
        ("test_metric_scopes_match_distinct_factor_union_and_page_limit",
         "test_metric_scope_completion_is_inclusive_point_in_time"),
        source_removed=True,
    ),
    Factor4ScriptMigration(
        "daily_revision_precondition_probe.py", _READ_MODULE, ("ENV-104-A",), "ASSERTED",
        "多修订数据发现由真实三点 MCP-DB 对账替代；没有自然修订时明确阻断，不伪造通过",
        ("test_environment_current_pointer_and_revision_are_unique",
         "test_environment_revision_three_point_boundary"),
        source_removed=True,
    ),
    Factor4ScriptMigration(
        "truerange_active_recheck.py", "tests/cases/factor4/test_formula_route_audit_migrations.py",
        ("CALC-510",), "ASSERTED",
        "TrueRange/ATR active route 的 detail、不可变公式 evidence 与发布结果由公式完整性和静态来源链断言替代",
        ("test_formula_integrity_and_source_chain",), source_removed=True,
    ),
    Factor4ScriptMigration(
        "targeted_formula_metadata_recheck.py", "tests/cases/factor4/test_formula_route_audit_migrations.py",
        ("CALC-510",), "ASSERTED",
        "当前 detail、公式 hash/version、required_fields、scope 与批次证据由静态一致性 Service 对账",
        ("test_formula_integrity_and_source_chain",), source_removed=True,
    ),
    Factor4ScriptMigration(
        "formula_integrity_audit.py", "tests/cases/factor4/test_formula_route_audit_migrations.py",
        ("CALC-510",), "ASSERTED",
        "逐 evidence 校验 completed Run、表达式语法、窗口声明和不可变元数据",
        ("test_formula_integrity_and_source_chain",), source_removed=True,
    ),
    Factor4ScriptMigration(
        "db_route_audit_once.py", "tests/cases/factor4/test_final_results.py",
        ("DB-602", "DB-604", "DB-608"), "ASSERTED",
        "route 身份、数值域、evidence、分区、六环境摘要和指标引用由最终结果 Service 断言替代",
        ("tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity",
         "test_final_result_numeric_domains_are_valid",
         "test_final_route_evidence_matches_persisted_result_columns",
         "test_final_environment_matrix_uses_declared_labels",
         "test_final_result_ranking_is_partitioned_and_repeatable"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "db613_targeted_closure.py", "tests/cases/factor4/test_final_results.py",
        ("DB-613",), "ASSERTED",
        "批次发布身份、环境摘要 route_count 与重复只读快照稳定性由正式 route 审计 Case 替代",
        ("tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity",
         "test_final_result_ranking_is_partitioned_and_repeatable"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "cross_scope_metrics_probe.py", _SUMMARY_MODULE, ("MCP-016",), "ASSERTED",
        "三scope单次/显式Run全部字段与resolved_scope、三good+missing批量、raw排名、零阈值candidate_count、final_score不可达过滤、PIT与slice均已业务断言",
        ("test_summary_final_fields_single_explicit_and_batch",
         "test_factor_rank_each_supported_metric_matches_db",
         "test_factor_rank_final_metric_filters_and_parent_theme_membership",
         "tests/cases/factor4/test_research_search_business.py::test_mixed_metric_batch_preserves_two_existing_factor_identities_and_local_error",
         "tests/cases/factor4/test_validity_migration_business.py::test_metric_and_validity_completion_point_in_time_boundary",
         "tests/cases/factor4/test_validity_migration_business.py::test_two_existing_validity_factors_and_absent_ref_preserve_single_batch_values",
         "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_three_exact_slice_scopes_reconcile_all_pages"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "validity_visibility_recheck.py", "tests/cases/factor4/test_validity_migration_business.py",
        ("MCP-019",), "ASSERTED",
        "active-route缺FK历史样本不直接报错；显式/省略Run真实validity重读DB外键、独立metrics逐字段、incomplete+missing批量隔离和complete对照均有断言",
        ("test_each_validity_shape_preserves_exact_ts_cs_evidence",
         "test_incomplete_validity_never_exposes_unsupported_valid_evidence",
         "test_metrics_remain_independent_of_incomplete_validity_foreign_keys",
         "test_incomplete_validity_and_absent_factor_remain_isolated_in_batch"), source_removed=True,
        excluded_branches=("旧备用Token是否可握手仅诊断凭据状态；正式会话fixture校验当前测试配置，不迁移过期凭据",),
    ),
    Factor4ScriptMigration(
        "validity_boundary_deep.py", "tests/cases/factor4/test_validity_migration_business.py",
        ("MCP-019",), "ASSERTED",
        "TS-only/CS-only/both精确metrics/validity、主有效维slice、省略/null Run、真实publication PIT三点、缺失ref批量与暂缓参数异常矩阵均已替代",
        ("test_each_validity_shape_preserves_exact_ts_cs_evidence", "test_primary_valid_dimension_slices_match_same_run_database",
         "test_optional_query_fields_preserve_aggregate_and_latest_identity",
         "test_invalid_query_and_nullable_symbol_do_not_expose_unrequested_evidence",
         "test_metric_and_validity_completion_point_in_time_boundary",
         "test_existing_and_absent_factor_batch_keep_item_identity_and_error_isolation"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "slice_reconcile_probe.py", "tests/cases/factor4/test_run_selection_and_slice_scopes.py",
        ("MCP-016",), "ASSERTED",
        "TS-symbol/aggregate及CS全页精确成员/时间/字段/分层JSON/顺序/去重与重放；cursor scope/limit/tamper及已排除结束边界有真实暂缓断言",
        ("test_three_exact_slice_scopes_reconcile_all_pages", "test_slice_cursor_cannot_cross_related_scopes",
         "test_slice_cursor_is_bound_to_query_and_signature", "test_exact_slice_end_equality_returns_contained_database_rows"), source_removed=True,
        excluded_branches=("读前后全库写水位不构成MCP写入证据；局部水位变化标记SNAPSHOT_DRIFT，不伪报产品错误",),
    ),
    Factor4ScriptMigration(
        "cross_scope_identity_probe.py", _SUMMARY_MODULE, ("MCP-016", "MCP-019", "MET-301"), "ASSERTED",
        "TS-symbol/direct与CS、parent child_aggregate的单次/显式Run/batch全字段身份、环境metrics/标签隔离、slice精确作用域已替代；字符串limit和结束相等边界明确暂缓",
        ("test_summary_final_fields_single_explicit_and_batch", "test_parent_child_aggregate_summary_matches_exact_database_fields",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_filter_and_final_fields",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_metric_label_filter_is_exact",
         "tests/cases/factor4/test_migrated_readonly_scripts.py::test_environment_tags_match_active_routes",
         "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_three_exact_slice_scopes_reconcile_all_pages",
         "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_exact_slice_end_equality_returns_contained_database_rows",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_semantically_invalid_read_arguments_return_explicit_errors"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "metrics_deep_minimal.py", _SUMMARY_MODULE, ("MCP-016", "MCP-019"), "ASSERTED",
        "动态scope、精确metrics single/batch与Run、signed rank重放、两good+missing validity batch、slices与formula/PIT均由正式业务断言替代",
        ("test_metric_scopes_match_distinct_factor_union_and_page_limit", "test_summary_final_fields_single_explicit_and_batch",
         "test_factor_rank_values_order_identity_and_latest_run", "test_formula_evidence_visibility_at_run_completion",
         "tests/cases/factor4/test_validity_migration_business.py::test_two_existing_validity_factors_and_absent_ref_preserve_single_batch_values",
         "tests/cases/factor4/test_validity_migration_business.py::test_explicit_slice_run_visibility_at_completion",
         "tests/cases/factor4/test_run_selection_and_slice_scopes.py::test_three_exact_slice_scopes_reconcile_all_pages"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "rank_functional_expansion.py", _SUMMARY_MODULE, ("MCP-016", "MCP-019"), "ASSERTED",
        "TS/CS九指标、三rank模式、单侧/零侧、主题命中/未命中、coverage/slice/OOS门槛及最新候选数、formula/metrics/scope/slice PIT均有真实断言",
        ("test_factor_rank_values_order_identity_and_latest_run", "test_factor_rank_each_supported_metric_matches_db",
         "test_factor_rank_requested_sides_and_repeat_are_stable", "test_factor_rank_zero_both_sides_is_rejected",
         "test_factor_rank_final_metric_filters_and_parent_theme_membership", "test_formula_evidence_visibility_at_run_completion",
         "test_metric_scope_completion_is_inclusive_point_in_time",
         "tests/cases/factor4/test_validity_migration_business.py::test_metric_and_validity_completion_point_in_time_boundary",
         "tests/cases/factor4/test_validity_migration_business.py::test_explicit_slice_run_visibility_at_completion"), source_removed=True,
        excluded_branches=("旧formula recorded_at前后预期已被后续裁决修正为Run completed_at，保留正确Oracle",
                           "run.config_ic_scope与实际TS/CS产出数量原为配置观察，未定义不可兼容契约，不计业务断言"),
    ),
    Factor4ScriptMigration(
        "resume_rank_adjudication.py", _SUMMARY_MODULE, ("MCP-016", "MCP-019"), "ASSERTED",
        "rank有效slice门槛使用最新行的max而非全历史max；完整排序候选与formula完成时间PIT、零侧合法拒绝均由动态DB和真实MCP断言替代",
        ("test_factor_rank_final_metric_filters_and_parent_theme_membership", "test_factor_rank_values_order_identity_and_latest_run",
         "test_formula_evidence_visibility_at_run_completion", "test_factor_rank_zero_both_sides_is_rejected"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "batch_cursor_boundary_probe.py", "tests/cases/factor4/test_protocol_deferred_business.py", ("MCP-005", "MCP-016", "MCP-018"), "ASSERTED",
        "真实动态cursor跨tool/limit绑定及detail空/混合缺失批量、metrics空/重复/混合缺失批量均有正式断言；异常与兼容分支保留deferred门禁，不执行旧报告cursor",
        ("test_catalog_cursor_scope_binding_and_tamper_rejection", "test_mixed_batch_keeps_existing_positions_and_isolates_missing_ref",
         "test_semantically_invalid_read_arguments_return_explicit_errors",
         "tests/cases/factor4/test_catalog_extension_business.py::test_detail_batch_preserves_mixed_kinds_input_order_and_duplicates",
         "tests/cases/factor4/test_validity_migration_business.py::test_metric_batch_empty_and_duplicate_input_contract",
         "tests/cases/factor4/test_validity_migration_business.py::test_existing_and_absent_factor_batch_keep_item_identity_and_error_isolation"), source_removed=True,
        excluded_branches=("旧detail duplicate只接受reject/dedup与现行已验证的保留请求顺序及重复项契约冲突，改为当前业务断言；任意协议错误不再算合法拒绝",),
    ),
    Factor4ScriptMigration(
        "calc508_env_met_reconcile.py", "tests/cases/factor4/test_backend_three_way_business.py", ("CALC-508", "ENV-108", "MET-310"), "ASSERTED",
        "所有active published最终费用空值/payload/route scope evidence、Backend-MCP-DB环境日期/PIT、summary完整数值/period瞬间及OpenAPI静态HMAC分别真实断言",
        ("test_published_cost_fields_and_route_scope_evidence_match_final_metrics",
         "test_backend_daily_exact_date_and_pit_match_mcp_database",
         "test_backend_summary_identity_values_and_period_instants_match_mcp_database",
         "test_backend_openapi_declares_internal_hmac_headers_not_user_bearer"), source_removed=True,
        excluded_branches=("用户限定最终结果：原始收益/持仓/换手/费用重算不在本轮范围；寻找原始字段与hash-only路径只是诊断",
                           "旧脚本固定transaction_cost=0.001不适用于其他冻结配置，不能把人工fixture当真实计算证据"),
    ),
    Factor4ScriptMigration(
        "catalog_search_functional_deep.py", _CATALOG_MODULE, ("MCP-006", "MCP-018"), "ASSERTED",
        "目录全分页/重放/updated严格边界/空末页/中文query，TS-CS研究scope成员/阈值/PIT及任一维度valid搜索全部正式业务断言",
        _CATALOG_NODES, source_removed=True,
        excluded_branches=("旧stats和library实体总数口径混用已纠正；numeric阈值仅属于factor_search，不向stats传schema未支持参数",
                           "全库前后count只能诊断漂移，不代表MCP无副作用"),
    ),
    Factor4ScriptMigration(
        "catalog_boundaries_resume_probe.py", _READ_MODULE, ("MCP-006", "MCP-018"), "ASSERTED",
        "updated_after严格前/等/后、目录全页/重放及explicit valid/invalid/unknown的完整catalog左连latest summary口径已真实对账",
        ("test_catalog_updated_after_boundary", "test_catalog_pagination_and_database_membership",
         _RESEARCH_MODULE + "::test_research_explicit_validity_statistics_include_unknown_catalog_entities",
         _PROTOCOL_DEFERRED + "::test_catalog_cursor_scope_binding_and_tamper_rejection"), source_removed=True,
        excluded_branches=("旧只读事务rollback本身是数据访问保证，不单独计业务Case",),
    ),
    Factor4ScriptMigration(
        "feedback_status_input_matrix.py", "tests/cases/factor4/test_feedback_status_business.py", ("MCP-006", "MCP-019", "DB-608"), "ASSERTED",
        "实际请求request_id精确关联调用者/active key/scopes，owned状态字段DB对账，other/missing隔离；13类非法输入已真实deferred Case",
        ("test_feedback_current_request_maps_to_active_key_owner_and_granted_scope",
         "test_feedback_owned_submission_status_counters_and_error_match_database",
         "test_feedback_missing_and_other_owner_ids_do_not_reveal_submission_data",
         "test_feedback_invalid_or_nonexistent_identifiers_are_rejected_without_leak"), source_removed=True,
        excluded_branches=("无归因的全库水位不作为单次只读证据；owned自然样本缺失明确数据前置，不创建反馈来伪造只读验收",),
    ),
    Factor4ScriptMigration(
        "status_permission_closure.py", "tests/cases/factor4/test_feedback_status_business.py", ("DB-608",), "ASSERTED",
        "当前PAT与精确审计请求/owner/required scope匹配、已有Backend登录identity门禁、HMAC静态header合约均实际断言；不复制旧固定BLOCKED/历史结论",
        ("test_feedback_current_request_maps_to_active_key_owner_and_granted_scope",
         "tests/cases/factor4/test_backend_three_way_business.py::test_backend_openapi_declares_internal_hmac_headers_not_user_bearer",
         _PROTOCOL_DEFERRED + "::test_feedback_write_tool_schema_is_inspected_without_invocation"), source_removed=True,
        excluded_branches=("ordinary browse PAT和只读DB账号分支原为固定BLOCKED且无独立授权凭据；不拿full_access/测试写账号替代，仍未验收",
                           "ENV110/MET305原返回硬编码历史计数/判词，非实时可执行断言；由环境和指标正式Case独立核验",
                           "HMAC runtime拒绝需要内部POST，不在只读迁移权限内；静态声明通过不等于运行权限通过",
                           "旧全库水位和artifact关键字计数是诊断，不计业务断言"),
    ),
    Factor4ScriptMigration(
        "tool_matrix_pit_probe.py", "tests/cases/factor4/test_cross_read_business.py", ("MCP-002", "MCP-005", "MCP-016", "MCP-017", "MCP-019"), "ASSERTED",
        "每个只读工具按实体业务Case对账，当前/历史completion/PIT、schema版本、feedback owner和双表示分别独立承接；异常/兼容/并发保持deferred",
        (*_CROSS_SCHEMA_NODES, *_CROSS_PROTOCOL_NODES, *_CROSS_CATALOG_NODES, *_CROSS_RESULT_NODES, *_CROSS_ENV_NODES, *_CROSS_AUXILIARY_NODES,
         "test_research_current_library_status_has_explicit_point_in_time_warning",
         "tests/cases/factor4/test_protocol_deferred_business.py::test_feedback_write_tool_schema_is_inspected_without_invocation"),
        source_removed=True,
        excluded_branches=("future_unseen将响应ID大于测试开始MAX或新Run视为泄漏不成立：共享环境合法新增不属于未来泄漏；改用真实available_at/completed_at/PIT身份",
                           "统计schema hash/enums/limits仅报告元数据；不是独立业务用例",
                           "不把读取write工具描述视为已执行feedback写链路；本次仅迁移原脚本实际只读分支"),
    ),
    Factor4ScriptMigration(
        "critical_readonly_gap_probe.py", "tests/cases/factor4/test_cross_read_business.py", ("ENV-101", "ENV-108", "REC-210", "MET-301", "MET-309"), "ASSERTED",
        "鉴权拒绝/敏感值不回显、fact与forecast DB/PIT/六概率/分页、推荐发布摘要及时间边界、metrics全部筛选与tags完整路由字段均已关联正式Case",
        (*_CROSS_PROTOCOL_NODES, *_CROSS_ENV_NODES),
        source_removed=True,
        excluded_branches=("全表before/after counts与水位变化无法证明请求是否写库，保留为诊断而非产品缺陷断言",),
    ),
    Factor4ScriptMigration(
        "cross_invariants_readonly.py", "tests/cases/factor4/test_schema_business.py", ("MCP-002", "MCP-005", "MCP-016", "MCP-017"), "ASSERTED",
        "approved schema完整字段映射/依赖/replay DB集合、选择器隔离与重读、formula全局输入、publication/tags/metric关联和feedback缺失隔离均为业务断言",
        (*_CROSS_SCHEMA_NODES, *_CROSS_PROTOCOL_NODES, *_CROSS_ENV_NODES, *_CROSS_AUXILIARY_NODES,
         "tests/cases/factor4/test_formula_catalog_business.py::test_all_active_formula_projections_and_approved_inputs",
         "tests/cases/factor4/test_formula_catalog_business.py::test_all_formula_evidence_source_fields_intervals_and_warnings",
         "tests/cases/factor4/test_formula_catalog_business.py::test_formula_catalog_and_evidence_internal_semantics"),
        source_removed=True,
        excluded_branches=("schema/feedback表前后全局计数相等不能证明调用无副作用；报告重试及诊断清单不是独立Case",),
    ),
    *(
        Factor4ScriptMigration(name, "tests/cases/factor4/test_cross_read_business.py", ("MCP-016", "MCP-017", "MCP-019", "ENV-101", "REC-210"), "ASSERTED",
            "目录筛选/分页/详情batch、独立research search+stats、TS/CS精确metrics/validity与混合batch、Run/PIT/slices、formula语义和无缓存重读、六概率/推荐/环境、schema/KB/universe均由分层用例承接；异常/并发单独deferred",
            (*_CROSS_SCHEMA_NODES, *_CROSS_PROTOCOL_NODES, *_CROSS_CATALOG_NODES, *_CROSS_RESULT_NODES, *_CROSS_ENV_NODES, *_CROSS_AUXILIARY_NODES),
            source_removed=True,
            excluded_branches=("全局表计数前后相同或变化只作漂移诊断，不推断单次请求是否写库",
                               "旧脚本quota/timeout仅记录环境状态；没有构造负载或执行限流测试，不计性能覆盖",
                               "旧AST将所有未识别标识符或任意聚合视为公式bug缺少方言/家族契约，已用公式模块逐类裁决与显式语义阻断替代",
                               "overall旧观察项已被用户TS或CS任一有效规则及真实search断言替代，不沿用旧规范假设"))
        for name in ("current_endpoint_deep_regression.py", "current_endpoint_functional_probe.py", "direct_functional_deep.py", "functional_extra_r0.py")
    ),
    Factor4ScriptMigration(
        "daily_reconcile_readonly.py", _READ_MODULE, ("ENV-101", "ENV-104", "ENV-109"), "ASSERTED",
        "fact/forecast 完整分页、全业务字段/审计时间字段、DB可见修订成员与逆序、去重、同一as-of结果核对",
        ("test_environment_daily_is_point_in_time_and_db_consistent",),
        source_removed=True,
        excluded_branches=("旧脚本强制相邻日期差1天缺少列表接口契约，改为与DB实际可见日期集合精确对账",
                           "全表前后水位只供漂移诊断，不能证明单次MCP是否写库"),
    ),
    Factor4ScriptMigration(
        "route_integrity_closure.py", _READ_MODULE, ("DB-602", "DB-604", "MET-311", "CALC-513"), "ASSERTED",
        "全历史route/metric/批次身份与保留、双无效排除及详情可查、冻结环境引用、推荐Backend/MCP/DB三方",
        ("test_environment_tags_match_active_routes",
         "test_environment_current_pointer_and_revision_are_unique",
         "test_environment_revision_three_point_boundary",
         "tests/cases/factor4/test_lifecycle_final_state.py::test_factor4_entity_schema_preserves_identity_and_revision_uniqueness",
         "tests/cases/factor4/test_lifecycle_final_state.py::test_route_history_retains_identity_and_deactivates_old_versions",
         "tests/cases/factor4/test_lifecycle_final_state.py::test_double_invalid_factors_remain_available_in_catalog_details",
         "tests/cases/factor4/test_environment_closure_business.py::test_each_label_metrics_routes_and_summary_share_full_publication_identity",
         "tests/cases/factor4/test_temporal_final_evidence.py::test_frozen_environment_dates_segments_and_metric_sample_counts",
         "tests/cases/factor4/test_route_environment_references.py::test_factor4_route_environment_references",
         "tests/cases/factor4/test_backend_three_way_business.py::test_backend_recommendations_publication_order_and_scores_match_mcp_database"),
        source_removed=True,
        excluded_branches=("schema枚举的FK名称非业务约束；current非唯一索引不代表当前数据违反单current规则",
                           "旧脚本将route.environment_date定义为UNDEFINED固定观察项，不构成独立通过证据"),
    ),
    Factor4ScriptMigration(
        "active_formula_semantic_recheck.py", "tests/cases/factor4/test_formula_catalog_business.py",
        ("CALC-510",), "ASSERTED",
        "全部 active eligible refs/重复缺失、MCP/DB 执行表达式/source、全局 approved raw fields 和非已知族发现；候选契约数据驱动阻断",
        ("test_all_active_formula_projections_and_approved_inputs", "test_catalog_semantic_candidates_are_conditionally_adjudicated",
         "test_formula_catalog_and_evidence_internal_semantics"), source_removed=True,
        excluded_branches=("未知族固定常数仅为候选，缺少 feature horizon 契约时不计独立公式正确性覆盖",),
    ),
    Factor4ScriptMigration(
        "dpo_formula_recheck.py", "tests/cases/factor4/test_formula_catalog_business.py",
        ("CALC-510",), "ASSERTED",
        "DPO 当前价格位移/窗口、精确公式身份、独立选取最新 factor_value_slice_metrics 与 completed evidence 同 Run/周期、有限数值",
        ("test_dpo_current_definition_uses_price_shift_and_declared_window", "test_dpo_latest_persisted_value_uses_completed_formula_run",
         "test_formula_family_current_and_completed_evidence_projections",
         "tests/cases/factor4/test_formula_route_audit_migrations.py::test_each_known_formula_family_definition_and_exact_evidence"), source_removed=True,
        excluded_branches=("不将本地 DPO 数学示例称为服务器原始行情逐点计算重放，当前验收范围仅最终结果",),
    ),
    Factor4ScriptMigration(
        "fixed_horizon_formula_recheck.py", "tests/cases/factor4/test_formula_catalog_business.py",
        ("CALC-510",), "ASSERTED",
        "7 个实际候选三层详情/精确 evidence 与 AST offsets；已知五个族按独立窗口契约判定，156469/88858 读取证据后报告真实 feature horizon 契约阻断",
        ("test_fixed_horizon_declared_and_completed_formula_candidates",
         "test_formula_family_current_and_completed_evidence_projections",
         "tests/cases/factor4/test_formula_route_audit_migrations.py::test_each_known_formula_family_definition_and_exact_evidence"), source_removed=True,
        excluded_branches=("156469/88858 的 1h evaluation identity 不自动等价于 1h feature horizon；未知契约只保留条件判定",),
    ),
    Factor4ScriptMigration(
        "iv_rv_definition_recheck.py", "tests/cases/factor4/test_formula_catalog_business.py",
        ("CALC-510",), "ASSERTED",
        "IV-RV 表达式/输入字段、summary/definition/executable 当前定义、全局 raw schema 与公开字段声明；全 evidence 来源字段/周期审计",
        ("test_iv_rv_all_detail_levels_and_global_input_schema", "test_formula_summary_validity_chain_for_actual_completed_run", "test_all_formula_evidence_source_fields_intervals_and_warnings",
         "test_formula_detail_levels_internal_semantics", "test_formula_catalog_and_evidence_internal_semantics",
         "tests/cases/factor4/test_formula_route_audit_migrations.py::test_each_known_formula_family_definition_and_exact_evidence"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "fixed_horizon_adjudication.py", "tests/cases/factor4/test_formula_catalog_business.py", ("CALC-510",), "ASSERTED",
        "声明窗口与 AST 原始依赖 offsets 独立计算，当前详情和精确 completed evidence 投影；不以旧 Run 代替当前批次",
        ("test_fixed_horizon_declared_and_completed_formula_candidates",
         "test_formula_family_current_and_completed_evidence_projections"), source_removed=True,
    ),
    Factor4ScriptMigration(
        "fixed_horizon_family_audit.py", "tests/cases/factor4/test_formula_catalog_business.py", ("CALC-510",), "ASSERTED",
        "按真实 parent/stem、相同表达式和不同声明 window 发现全部家族候选，逐候选精确 evidence；没有 feature 契约不推断缺陷",
        ("test_catalog_semantic_candidates_are_conditionally_adjudicated",), source_removed=True,
        excluded_branches=("同族同表达式不同 evaluation window 不是充分缺陷证据，未知家族语义不计数值重算覆盖",),
    ),
    Factor4ScriptMigration(
        "semantic_formula_scan.py", "tests/cases/factor4/test_formula_catalog_business.py", ("CALC-510",), "ASSERTED",
        "15 类整库候选、wrapper/raw/derived 字段、频率/区间、future/center/annualization/家族及全部 evidence 元数据；未发布目录/历史定义/不明语义显式条件阻断",
        ("test_catalog_semantic_candidates_are_conditionally_adjudicated", "test_all_formula_evidence_source_fields_intervals_and_warnings",
         "test_formula_catalog_and_evidence_internal_semantics"), source_removed=True,
        excluded_branches=("future/annualization/frequency/未知派生字段原为诊断候选；缺少因果/频率/算子契约的候选不计产品计算通过",),
    ),
    Factor4ScriptMigration(
        "recheck_aggregate_window_candidates.py", "tests/cases/factor4/test_formula_catalog_business.py", ("CALC-510", "MCP-016"), "ASSERTED",
        "两实际聚合候选 exact completed formula、TS/CS 实存 symbol scopes 全字段最终指标、聚合 AST 分类；缺失 aggregate/window 契约独立阻断",
        ("test_formula_summary_validity_chain_for_actual_completed_run", "test_catalog_semantic_candidates_are_conditionally_adjudicated"), source_removed=True,
        excluded_branches=("无界 aggregate 的输入究竟是完整序列还是预切窗口无法仅凭公式证明；不把候选自动变成缺陷",),
    ),
    Factor4ScriptMigration(
        "targeted_5921_recheck.py", "tests/cases/factor4/test_formula_catalog_business.py", ("CALC-510", "MCP-016", "MCP-019"), "ASSERTED",
        "以实际 completed Run 替代硬编码历史 Run，公式 hash/lookback/source、错误 window NOT_FOUND、TS/CS 最终字段、同 Run validity 与当前详情独立对账",
        ("test_formula_summary_validity_chain_for_actual_completed_run", "test_targeted_formula_current_definition_projects_its_current_window",
         "test_formula_detail_levels_internal_semantics"), source_removed=True,
    ),
    *(
        Factor4ScriptMigration(name, "", (), "NON_CASE_TOOL",
            "历史报告合并/恢复工具；不是业务 Case，保留以便读取旧证据")
        for name in ("final_coverage_merge.py", "recover_readonly_invariant_report.py")
    ),
)


def factor4_script_migrations() -> tuple[Factor4ScriptMigration, ...]:
    """返回已审查映射和未审查文件；未知来源必须为 REMAINING，永不自动升级为已迁移。

    只读取本地文件名，无网络/DB 操作。已删除来源仍保留映射，便于审计和恢复；
    状态与 pytest 实际执行结果分开，PARTIAL/登记自检不计业务完成。
    """
    root = Path(__file__).resolve().parents[1] / "tmp"
    known = {item.script_name for item in _SCRIPT_MIGRATIONS}
    remaining = tuple(
        Factor4ScriptMigration(script.name, "", (), "REMAINING",
                              "尚未逐分支审查和实现业务替代；不因登记或文件存在而视为迁移")
        for script in sorted(root.glob("*.py"))
        if script.name not in known
    )
    return (*_SCRIPT_MIGRATIONS, *remaining)


_MODULE_RANGES: tuple[tuple[str, str, int, int], ...] = (
    ("MCP", "mcp.protocol", 1, 19),
    ("HMAC", "security.hmac", 1, 7),
    ("ENV", "environment", 101, 112),
    ("REC", "recommendation", 201, 213),
    ("MET", "metric", 301, 311),
    ("LIFE", "lifecycle", 400, 418),
    ("CALC", "calculation", 501, 513),
    ("DB", "database", 601, 613),
)

_BLOCKED = frozenset({"CALC-502", "CALC-503", "CALC-505", "CALC-508", "CALC-509", "CALC-511", "CALC-512"})
_DEFERRED = frozenset({"MCP-012", "MCP-013", "MCP-014", "MCP-015"})


def historical_factor4_cases() -> tuple[Factor4CaseSpec, ...]:
    """返回规约中的 107 条唯一业务用例。

    返回值按文档模块和编号升序排列。脚本映射只能证明编号的某些分支已实现，
    不足以宣布整条原始需求或真实执行通过；该函数只读本地迁移清单。
    """

    represented = {
        "-".join(case_id.split("-")[:2])
        for source in factor4_script_migrations()
        if source.status in {"ASSERTED", "PARTIAL"} and source.test_nodes
        for case_id in source.source_case_ids
    }
    represented.update({"CALC-501", "CALC-506", "CALC-507", "CALC-510", "CALC-513"})
    cases: list[Factor4CaseSpec] = []
    for prefix, module, start, end in _MODULE_RANGES:
        for number in range(start, end + 1):
            case_id = f"{prefix}-{number:03d}" if prefix in {"MCP", "HMAC"} else f"{prefix}-{number}"
            if case_id in _DEFERRED:
                state: CoverageState = "DEFERRED_SCOPE"
                note = "异常、性能或并发场景按当前测试范围暂缓"
            elif case_id in represented:
                state = "PARTIALLY_IMPLEMENTED"
                note = "已有可追溯业务断言；迁移完整不等于原需求全部分支完成，执行结果以本轮JUnit为准"
            elif case_id in _BLOCKED:
                state = "BLOCKED_DATA_OR_FIXTURE"
                note = "需要原始数据、版本样本或受控测试 fixture"
            else:
                state = "NOT_IMPLEMENTED"
                note = "尚未落地对应真实接口或数据库 Case"
            cases.append(Factor4CaseSpec(case_id, module, state, note))
    return tuple(cases)


def factor4_case_summary(
    cases: tuple[Factor4CaseSpec, ...] | None = None,
) -> Mapping[str, int]:
    """统计各落地状态的用例数量。

    ``cases`` 可传入自定义登记表；不传时统计历史 107 条。返回普通字典，便于
    报告序列化；不执行 I/O。
    """

    selected = cases if cases is not None else historical_factor4_cases()
    return dict(Counter(case.state for case in selected))


__all__ = [
    "Factor4CaseSpec", "Factor4ScriptMigration", "CoverageState",
    "historical_factor4_cases", "factor4_case_summary", "factor4_script_migrations",
]
