#!/usr/bin/env python3
"""Merge the 100-case Factor 4.0 coverage baseline with authoritative closures."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "factor4-resume"
BASELINE_PATH = REPORT_ROOT / "20260904T121637+0800-coverage-audit/coverage.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ALLOWED_STATUSES = {"PASS", "FAIL", "BLOCKED", "EXCLUDED"}
TOKEN_PATTERN = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


SOURCE_REPORTS: list[dict[str, Any]] = [
    {
        "name": "catalog-boundaries",
        "path": "reports/factor4-resume/20260904T120051+0800-catalog-boundaries/adjudicated-summary.json",
        "native_result": "3 PASS",
        "formal_case_ids": ["MCP-006", "MCP-018"],
    },
    {
        "name": "protocol-gaps",
        "path": "reports/factor4-resume/20260904T044449Z-protocol-gaps/adjudicated-summary.json",
        "native_result": "120 PASS, 1 local-only PASS, 3 dependency BLOCKED, 1 data BLOCKED, 4 NOT_APPLICABLE, 0 FAIL",
        "formal_case_ids": ["MCP-006", "MCP-009", "MCP-010", "MCP-011", "MCP-014", "MCP-015"],
    },
    {
        "name": "tool-matrix-pit",
        "path": "reports/factor4-resume/20260904T124800+0800-tool-matrix-pit/results.json",
        "native_result": "MCP-016: 19 COVERED, 2 BLOCKED, 1 NOT_APPLICABLE; MCP-017 PASS; MCP-019: 12 PASS, 1 BLOCKED",
        "formal_case_ids": ["MCP-016", "MCP-017", "MCP-019"],
    },
    {
        "name": "route-integrity",
        "path": "reports/factor4-resume/20260904T123910+0800-route-integrity-closure/results.json",
        "native_result": "4 BLOCKED, 0 FAIL",
        "formal_case_ids": ["DB-602", "DB-604", "MET-311", "CALC-513"],
    },
    {
        "name": "temporal-oracle",
        "path": "reports/factor4-resume/20260904T130856+0800-temporal-oracle-closure/adjudicated-summary.json",
        "native_result": "2 BLOCKED_DATA_PRECONDITION, 0 FAIL",
        "formal_case_ids": ["CALC-502", "CALC-503"],
    },
    {
        "name": "status-permission",
        "path": "reports/factor4-resume/20260904T133229+0800-status-permission-closure/adjudicated-summary.json",
        "native_result": "3 BLOCKED, 0 FAIL",
        "formal_case_ids": ["ENV-110", "MET-305", "DB-608"],
    },
    {
        "name": "ranking-parent-snapshot",
        "path": "reports/factor4-resume/20260904T134440+0800-ranking-parent-snapshot-closure/adjudicated-summary.json",
        "native_result": "2 BLOCKED_DATA_PRECONDITION, 0 FAIL",
        "formal_case_ids": ["CALC-507", "CALC-511"],
    },
    {
        "name": "fixed-horizon",
        "path": "reports/factor4-resume/20260904T121645+0800-fixed-horizon-adjudication/results.json",
        "native_result": "CALC-510: 1 factor PASS, 4 factors FAIL",
        "formal_case_ids": ["CALC-510"],
    },
    {
        "name": "calc508-env108-met310",
        "path": "reports/factor4-resume/20260904T040938Z-calc508-env108-met310/adjudicated-summary.json",
        "native_result": "CALC-508 BLOCKED; ENV-108 FAIL; MET-310 FAIL",
        "formal_case_ids": ["CALC-508", "ENV-108", "MET-310", "DB-605"],
    },
    {
        "name": "db613-targeted-closure",
        "path": "reports/factor4-resume/20260904T140945+0800-db613-targeted-closure/adjudicated-summary.json",
        "native_result": "DB-613 FAIL: two stable reads each returned stored route_count=0 and exact active eligible route count=86",
        "formal_case_ids": ["DB-613"],
    },
]


UPDATES: dict[str, dict[str, Any]] = {
    "MCP-006": {
        "status": "BLOCKED",
        "classification": "PARTIAL_COVERAGE",
        "reason": (
            "未知字段、枚举及多数已声明 limit 边界均通过；但 environment_get_recommendations "
            "的合法 baseline/min/max 被 DEPENDENCY_UNAVAILABLE 阻断，反馈状态又没有调用者自有 "
            "submission，必需正向分支尚未闭环。"
        ),
        "covered": ["120 个协议/输入子断言通过；catalog updated_after 严格边界通过。"],
        "not_covered": ["推荐合法边界的业务成功分支。", "反馈状态的调用者自有 submission 成功分支。"],
        "remaining_actions": ["恢复 factor_reader_db，并提供当前调用者自有 feedback submission 后重跑被阻断分支。"],
    },
    "MCP-009": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": (
            "协议闭环对已发现只读工具执行调用，前后十张业务/任务表水位一致；数据库核查使用 "
            "READ ONLY 事务并回滚，未观察到业务写入或隐式计算任务。"
        ),
        "covered": ["全部具备可调用参数的已发现只读工具及其错误分支。"],
        "not_covered": [],
        "remaining_actions": [],
    },
    "MCP-010": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": "同一客户端复用与独立重连均通过；服务未声明 MCP-Session-Id，状态会话强制分支不适用。",
    },
    "MCP-011": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": "malformed JSON/envelope、未知 method、错误参数容器、重复 ID 与未知协议版本均被确定性处理。",
    },
    "MCP-014": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": (
            "最大有界响应和 cursor 续页均通过，未出现半截 JSON、重复或串页；端点明确以 406 拒绝 "
            "SSE-only，因此服务端异常 SSE 分支按契约 NOT_APPLICABLE，离线解析器另行 fail-closed。"
        ),
        "covered": ["有界大响应、截断/分页连续性和客户端异常流解析。"],
        "not_covered": [],
        "remaining_actions": [],
    },
    "MCP-015": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": "固定 as_of 下四个独立并发客户端与前后串行控制的规范化业务快照一致。",
    },
    "MCP-016": {
        "status": "BLOCKED",
        "classification": "PARTIAL_COVERAGE",
        "reason": (
            "22 个工具均已有矩阵行和明确分类：19 COVERED、2 BLOCKED、1 NOT_APPLICABLE。"
            "recommendations 受依赖阻断，feedback status 缺调用者自有 submission；按严格口径整个正式用例仍为 BLOCKED。"
        ),
        "covered": ["22/22 工具完成 Schema、权限边界和矩阵登记；19 个只读工具完成最小合法业务成功。"],
        "not_covered": ["environment_get_recommendations 最小合法业务成功。", "get_feedback_submission_status 所有者成功读取。"],
        "remaining_actions": ["恢复 factor_reader_db，并提供调用者自有 feedback submission。"],
    },
    "MCP-017": {
        "status": "PASS",
        "classification": "VERIFIED",
        "reason": "47 个成功/错误响应的 content 与 structuredContent 均一致，错误 envelope 映射无异常。",
    },
    "MCP-019": {
        "status": "BLOCKED",
        "classification": "PARTIAL_COVERAGE",
        "reason": "13 个支持 PIT 的读取工具中 12 个通过；environment_get_recommendations 当前/未来分支均被 factor_reader_db 阻断。",
        "covered": ["12 个工具的历史可见性、未来标识与 warning 保留均通过。"],
        "not_covered": ["推荐工具在 publication 边界及未来时点的成功业务结果。"],
        "remaining_actions": ["恢复 factor_reader_db 后重跑推荐 PIT 矩阵。"],
    },
    "ENV-108": {
        "status": "FAIL",
        "classification": "PRODUCT_DEFECT_BACKEND_MCP_DIVERGENCE",
        "reason": (
            "Backend 不带 environment_date 时能列出当前行，但带文档化精确日期过滤后遗漏 fact id=2182 "
            "和 forecast id=2183；同条件 MCP 与 DB 均命中。"
        ),
    },
    "ENV-110": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DOC",
        "reason": (
            "当前 forecast 数据映射一致，但 MCP 与 Backend 均未发布行级输出字段的 required/nullable/location "
            "契约；DB effective_from/effective_to 可空且样本全为 NULL，不能把省略与显式 null 定性为缺陷。"
        ),
        "covered": ["现有 forecast 样本的 MCP、Backend 与 DB 映射一致。"],
        "not_covered": ["窗口字段 required/nullable/location 与 effective window 的权威输出契约。"],
        "remaining_actions": ["发布 forecast 输出 Schema，并明确 effective_from/effective_to、forecast_date 与 horizon 映射。"],
    },
    "MET-305": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "全表只有 success 和 insufficient_sample，两类状态的证据、空值、payload 镜像及 route 准入均正确；"
            "没有 failed metric，技术失败状态分支无法裁决。"
        ),
        "covered": ["success 与 insufficient_sample 两个状态分支。"],
        "not_covered": ["failed metric 的错误证据、发布阻断和 route 排除。"],
        "remaining_actions": ["提供一个可控 failed metric 或权威失败批次。"],
    },
    "MET-310": {
        "status": "FAIL",
        "classification": "PRODUCT_DEFECT_TIME_SERIALIZATION_DIVERGENCE",
        "reason": "两个 metric 的身份三方一致，但 Backend period_start/period_end 比 DB metrics_json 的明确 UTC 时点及 MCP 固定早 8 小时。",
    },
    "MET-311": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DEPENDENCY",
        "reason": (
            "DB oracle 确认 2,769 个 TS/CS 双无效因子标签组均未进入 86 条 active route；但 MCP/Backend "
            "推荐都未返回 items，无法完成端到端排除断言。"
        ),
        "covered": ["双无效因子与 active route 的 DB 交集为 0，抽样因子详情可查。"],
        "not_covered": ["MCP 与 Backend 推荐 items 的双无效因子排除。"],
        "remaining_actions": ["恢复 factor_reader_db 后重放同一推荐并核对 items。"],
    },
    "CALC-502": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "快照正确保留不连续日历区段，metric day counts 也一致；但缺版本化 gap-handling 规则以及逐 bar "
            "收益、仓位和换手输入，无法独立判断收益、年化、换手和回撤是否正确处理缺口。"
        ),
        "covered": ["日历片段、分页排序、边界和 metric 天数一致性。"],
        "not_covered": ["缺口期间暴露语义及逐 bar 计算重放。"],
        "remaining_actions": ["提供 gap-handling 契约及逐 bar return/position/turnover 输入。"],
    },
    "CALC-503": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "未发现明确未来时间戳：冻结环境修订、OOS fold 和 direction 冻结边界均早于 as_of；但原始 bar/label/"
            "forward-return、训练成员和归一化拟合参数不可访问，仍不能完成全链路无泄漏证明。"
        ),
        "covered": ["环境可见性、OOS 时间边界和 direction 冻结时间。"],
        "not_covered": ["训练输入成员、标签/前瞻收益和 scaler 拟合来源的独立重建。"],
        "remaining_actions": ["提供原始 bar、label、forward-return、训练成员及归一化参数，并保留旧 published control。"],
    },
    "CALC-507": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "当前唯一分区 all/WIDE_RANGE/default/2026-09-02 的 86 条 rank 为 1..86、score 无逆序，"
            "8 个同分组的稳定顺序也无违规；但没有第二分区或历史 inactive route，无法验证隔离与历史排除。"
        ),
        "covered": ["唯一现存分区的身份、连续 rank、score 顺序和同分稳定顺序。"],
        "not_covered": ["跨 market/label/profile/as_of 分区隔离。", "历史 publication route 排除。"],
        "remaining_actions": ["提供第二排名分区以及保留 inactive route 的历史 publication。"],
    },
    "CALC-508": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "5,724 条 metric 成本字段和 86 条 route 追溯一致；系统未暴露 gross/raw strategy return、"
            "positions/signals 或逐期收益，无法独立重算成本扣除和 net Sharpe。"
        ),
        "covered": ["成本配置、metric payload 镜像、状态空值及 route evidence 追溯。"],
        "not_covered": ["transaction cost、net return 和 net Sharpe 的独立数值重算。"],
        "remaining_actions": ["提供 gross/raw return，或 positions/signals 与 period return 序列。"],
    },
    "CALC-510": {
        "status": "FAIL",
        "classification": "PRODUCT_DEFECT_FORMULA_SEMANTICS",
        "reason": (
            "3 个 active DPO 因子把均线而非价格序列做位移，与独立 DPO oracle 不等；另有 sub_factor:181/183/274/276 "
            "声明 48/72 bars，但当前公式和 completed-run 原始依赖跨度均只有 24 bars。sub_factor:180 的 24-bar 版本通过。"
        ),
    },
    "CALC-511": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "已发布 batch 6 的 477 个直接子因子和 5,724 条 metric 身份/版本全部与冻结快照一致；该批次没有母因子。"
            "含母子关系的两个历史批次均 cancelled、unpublished 且无 metric，不能证明母因子评估使用了建批时全部子因子。"
        ),
        "covered": ["已发布批次的直接子因子冻结身份、定义版本、执行版本、批次和配置对账。"],
        "not_covered": ["已发布母因子及其全部冻结子因子的实际评估。"],
        "remaining_actions": ["提供含 parent、frozen children 且已完成 metrics 的 published/terminal batch。"],
    },
    "CALC-513": {
        "status": "BLOCKED",
        "classification": "PARTIAL_COVERAGE",
        "reason": (
            "86 条 route 的 batch/publication/metric/factor 引用和 727 个冻结环境成员均一致；但推荐依赖不可用，"
            "且契约未定义 route.environment_date 是 snapshot member date 还是 publication-effective date。"
        ),
        "covered": ["route 身份引用、冻结 daily revision 可见性和 missing_dates 互斥。"],
        "not_covered": ["live recommendation publication replay。", "route.environment_date 的权威日期语义。"],
        "remaining_actions": ["恢复 factor_reader_db，并明确 route.environment_date 契约后重放。"],
    },
    "DB-602": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "2,105 行 daily 没有 revision/current 重复，revision 唯一键存在；但所有记录均为 revision=1，"
            "current 索引本身非唯一，无法只读证明历史保留和等价的服务层 current 唯一保证。"
        ),
        "covered": ["当前数据的 revision 键和 current 组合无重复。"],
        "not_covered": ["同一业务键 revision 1/2 的历史保留与 current 切换。"],
        "remaining_actions": ["提供同一 date+kind 的 revision 1/2 专用样本。"],
    },
    "DB-604": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": "当前 86 条 active route 唯一且 metric 关联正确；测试库只有一个 publication、零 inactive route，无法观察切换和历史保留。",
        "covered": ["当前 publication 内 route 唯一性和 metric 外键/身份。"],
        "not_covered": ["两版 publication 切换及 inactive 历史 route 保留。"],
        "remaining_actions": ["提供第二 publication 和 inactive route 历史。"],
    },
    "DB-605": {
        "status": "FAIL",
        "classification": "AGGREGATED_RECONCILIATION_FAILURE",
        "reason": "三方对账因 ENV-108 的 Backend 精确日期漏行和 MET-310 的时间边界偏移失败；这是两个上游根因的汇总表现，不另计缺陷组。",
    },
    "DB-608": {
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "未确认权限绕过。Backend HMAC 边界已由 OpenAPI 明确；当前 PAT 是特权凭据，当前 DB 账号是允许写入的测试账号。"
            "缺普通 browse PAT 与独立只读 DB 凭据，无法把正式用例判 PASS。"
        ),
        "covered": ["特权 PAT scope、Backend HMAC 契约以及当前测试写账号 grants 的身份边界。"],
        "not_covered": ["普通 browse PAT 的写权限拒绝。", "独立只读 DB 账号的 DML/DDL 拒绝。"],
        "remaining_actions": ["提供普通 browse PAT 与独立 read-only DB credential。"],
    },
    "DB-613": {
        "status": "FAIL",
        "classification": "PRODUCT_DEFECT_PUBLISHED_SUMMARY_DRIFT",
        "reason": (
            "只读定向复验在同一事务内连续两次读取 published success batch 6："
            "environment_status.WIDE_RANGE.route_count 均为 0，而按同一 batch、publication_uid、"
            "publish_version、market_scope 与 label_code 精确统计的 active eligible route 均为 86；"
            "两次快照稳定且身份错配数为 0。"
        ),
    },
}

# Only reports that expose an explicit formal case verdict may replace the baseline case.
# Protocol child probes and per-tool matrix rows remain related evidence, not aggregate overrides.
FORMAL_UPDATE_IDS = {
    "MCP-017",
    "ENV-108",
    "ENV-110",
    "MET-305",
    "MET-310",
    "MET-311",
    "CALC-502",
    "CALC-503",
    "CALC-507",
    "CALC-508",
    "CALC-510",
    "CALC-511",
    "CALC-513",
    "DB-602",
    "DB-604",
    "DB-608",
    "DB-613",
}


DEFECT_GROUPS: list[dict[str, Any]] = [
    {
        "defect_id": "F4-ENV-BACKEND-EXACT-FILTER",
        "severity": "P1",
        "formal_case_ids": ["ENV-108", "DB-605"],
        "primary_case_id": "ENV-108",
        "confirmed_fact": "Backend 的精确 environment_date 过滤遗漏 MCP 与 DB 均能命中的 fact id=2182 和 forecast id=2183。",
        "root_cause_status": "实现层根因尚未直接验证；只能确认 Backend exact-filter 分支行为错误。",
        "evidence_paths": [SOURCE_REPORTS[8]["path"]],
    },
    {
        "defect_id": "F4-METRIC-PERIOD-TZ",
        "severity": "P1",
        "formal_case_ids": ["MET-310", "DB-605"],
        "primary_case_id": "MET-310",
        "confirmed_fact": "Backend 的 period_start/period_end 比 DB metrics_json 的明确 UTC 时点及 MCP 固定早 8 小时。",
        "root_cause_status": "疑似 naive datetime 本地化，但未读取部署代码，具体代码根因尚未验证。",
        "evidence_paths": [SOURCE_REPORTS[8]["path"]],
    },
    {
        "defect_id": "F4-DPO-FORMULA",
        "severity": "P1",
        "formal_case_ids": ["CALC-510"],
        "primary_case_id": "CALC-510",
        "confirmed_fact": "sub_factor:161104/161106/161108 的 active 存储公式移动均线而非价格序列，与标准 DPO 独立 oracle 数值不等。",
        "root_cause_status": "已确认公式语义错误；未把历史 VWAP 数据计入本缺陷。",
        "evidence_paths": ["reports/factor4-deep/20260904T034927Z-dpo-formula-recheck/report.json"],
    },
    {
        "defect_id": "F4-FIXED-HORIZON-FORMULA",
        "severity": "P1",
        "formal_case_ids": ["CALC-510"],
        "primary_case_id": "CALC-510",
        "confirmed_fact": "sub_factor:181/183/274/276 声明 48/72 bars，但当前公式与 completed-run 原始依赖跨度均仅 24 bars。",
        "root_cause_status": "已确认窗口参数未进入四个因子的公式语义；24-bar sub_factor:180 已通过。",
        "evidence_paths": [SOURCE_REPORTS[7]["path"]],
    },
    {
        "defect_id": "F4-PUBLISHED-ROUTE-COUNT",
        "severity": "P1",
        "formal_case_ids": ["DB-613"],
        "primary_case_id": "DB-613",
        "confirmed_fact": "published success batch 6 的 environment_status.WIDE_RANGE.route_count=0，但同 batch/publication/version 存在 86 条 active eligible route。",
        "root_cause_status": "已确认发布摘要漂移；具体写入代码根因尚未验证。",
        "current_evidence_status": "CONFIRMED",
        "evidence_paths": [SOURCE_REPORTS[9]["path"]],
    },
]


EXTERNAL_PREREQUISITES: list[dict[str, Any]] = [
    {
        "prerequisite_id": "EXT-READER-DB",
        "description": "恢复 factor_reader_db，使推荐读取能够返回业务 items。",
        "case_ids": ["MCP-006", "MCP-016", "MCP-019", "REC-203", "REC-204", "REC-210", "REC-211", "MET-311", "CALC-513"],
    },
    {
        "prerequisite_id": "EXT-FEEDBACK-OWNER",
        "description": "提供当前调用者自有 feedback submission；若需创建，还需开启受控 R1。",
        "case_ids": ["MCP-006", "MCP-007", "MCP-016"],
    },
    {
        "prerequisite_id": "EXT-FAULT-INJECTION",
        "description": "提供可控慢请求/取消点，以及明确安全阈值的限流或 quota fixture。",
        "case_ids": ["MCP-012", "MCP-013"],
    },
    {
        "prerequisite_id": "EXT-REVISION-PUBLICATION-HISTORY",
        "description": "提供 rev1/rev2、第二 publication、inactive route 和第二排名分区等历史样本。",
        "case_ids": ["ENV-104", "REC-213", "CALC-507", "DB-602", "DB-604"],
    },
    {
        "prerequisite_id": "EXT-RECOMMENDATION-BOUNDARIES",
        "description": "提供双 market_scope、第二 profile、阈值前/等于/后及同分稳定键样本。",
        "case_ids": ["REC-205", "REC-206", "REC-207", "REC-208"],
    },
    {
        "prerequisite_id": "EXT-RAW-CALCULATION-INPUTS",
        "description": "提供 gap policy、逐 bar return/position/turnover、训练成员、scaler、gross/raw return 等独立 oracle 输入。",
        "case_ids": ["CALC-502", "CALC-503", "CALC-508"],
    },
    {
        "prerequisite_id": "EXT-STATE-SAMPLES",
        "description": "提供 failed metric，以及含 parent/frozen children 且已完成 metrics 的 published/terminal batch。",
        "case_ids": ["MET-305", "CALC-511"],
    },
    {
        "prerequisite_id": "EXT-CONTRACTS",
        "description": "发布 forecast 字段契约、publication mode 契约，并定义 route.environment_date 的权威语义。",
        "case_ids": ["ENV-110", "REC-209", "LIFE-400", "LIFE-410", "LIFE-411", "LIFE-412", "LIFE-413", "LIFE-414", "LIFE-415", "CALC-513"],
    },
    {
        "prerequisite_id": "EXT-R1-HMAC-SCHEDULER",
        "description": "开启 ALLOW_TEST_WRITES，提供授权 JWT/HMAC、可清理专用 fixture、中途失败注入和 Scheduler URL。",
        "case_ids": [
            "ENV-112", "REC-213", "LIFE-401", "LIFE-402", "LIFE-403", "LIFE-404", "LIFE-405",
            "LIFE-406", "LIFE-407", "LIFE-408", "LIFE-409", "LIFE-410", "LIFE-411", "LIFE-412",
            "LIFE-413", "LIFE-414", "LIFE-415", "LIFE-416", "LIFE-417", "LIFE-418", "DB-607",
            "DB-610", "DB-611", "DB-612",
        ],
        "non_formal_dependencies": ["HMAC-001~HMAC-007"],
    },
    {
        "prerequisite_id": "EXT-LEAST-PRIVILEGE-CREDENTIALS",
        "description": "提供普通 browse PAT 与独立 read-only DB credential；当前特权 PAT/测试写账号不能替代。",
        "case_ids": ["DB-608"],
    },
]


ADJUDICATION_GAPS: list[dict[str, Any]] = [
    {
        "gap_id": "AGGREGATE-MCP-009",
        "case_ids": ["MCP-009"],
        "description": "协议报告包含 DB 水位子断言，但没有新的 MCP-009 aggregate formal verdict；按指令保留基线 BLOCKED。",
    },
    {
        "gap_id": "AGGREGATE-MCP-014",
        "case_ids": ["MCP-014"],
        "description": "大响应/分页子项通过且 SSE 子项不适用，但没有新的 MCP-014 aggregate formal verdict；按指令保留基线 BLOCKED。",
    },
]


SUPERSEDED_REPORTS: list[dict[str, str]] = [
    {
        "path": "reports/factor4-resume/20260904T121637+0800-coverage-audit/coverage.json",
        "superseded_by": "this final coverage report",
        "reason": "保留为 100-case 编号基线；其总体状态被随后专项闭环更新。",
    },
    {
        "path": "reports/factor4-resume/20260904T115155+0800-rank-adjudication/results.json",
        "superseded_by": "reports/factor4-resume/20260904T115240+0800-rank-adjudication/results.json",
        "reason": "旧 RANK-FILTER-SLICES-EQUAL FAIL 使用了错误的集合 oracle；正确 top/bottom oracle 后通过。",
    },
    {
        "path": "reports/factor4-resume/20260904T115749+0800-catalog-boundaries/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[0]["path"],
        "reason": "旧 DB oracle 漏算 unknown catalog。",
    },
    {
        "path": "reports/factor4-resume/20260904T115925+0800-catalog-boundaries/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[0]["path"],
        "reason": "旧 DB oracle 未应用 interval scope；对齐后 valid/invalid/unknown 全部通过。",
    },
    {
        "path": "reports/factor4-resume/20260904T124425+0800-tool-matrix-pit/results.json",
        "superseded_by": SOURCE_REPORTS[2]["path"],
        "reason": "同一专项的较早运行，以 12:48 最新完整运行作为权威证据。",
    },
    {
        "path": "reports/factor4-resume/20260904T132822+0800-status-permission-closure/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[5]["path"],
        "reason": "DB-608 早版混合了尚未核实的 HMAC 文档判断。",
    },
    {
        "path": "reports/factor4-resume/20260904T133018+0800-status-permission-closure/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[5]["path"],
        "reason": "DB-608 最终裁决已收敛为只缺 ordinary PAT 和 read-only DB credential。",
    },
    {
        "path": "reports/factor4-resume/20260904T134116+0800-ranking-parent-snapshot-closure/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[6]["path"],
        "reason": "旧 CALC-511 FAIL 错把冻结 definition version 与 executable version 直接比较，制造 5,724 个误报。",
    },
    {
        "path": "reports/factor4-resume/20260904T115138+0800-rank-adjudication/",
        "superseded_by": "reports/factor4-resume/20260904T115240+0800-rank-adjudication/results.json",
        "reason": "空运行目录，没有可引用证据。",
    },
    {
        "path": "reports/factor4-resume/20260904T134037+0800-ranking-parent-snapshot-closure/",
        "superseded_by": SOURCE_REPORTS[6]["path"],
        "reason": "空失败运行目录，没有可引用证据。",
    },
    {
        "path": "reports/factor4-resume/20260904T140455+0800-final-coverage/coverage.json",
        "superseded_by": "this final coverage report",
        "reason": "旧汇总尚未收口 DB-613 的证据冲突；定向只读复验已经给出最终裁决。",
    },
    {
        "path": "reports/factor4-resume/20260904T140737+0800-db613-targeted-closure/adjudicated-summary.json",
        "superseded_by": SOURCE_REPORTS[9]["path"],
        "reason": "主结论有效，但辅助身份错配查询未限定 label_code 且普通不等比较会漏掉 NULL；最新运行补全了守卫。",
    },
]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one evidence file."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_update(case: dict[str, Any], update: dict[str, Any]) -> None:
    """Apply one authoritative formal-case adjudication in place."""

    case["status"] = update["status"]
    case["classification"] = update["classification"]
    case["reason"] = update["reason"]
    if "covered" in update or "not_covered" in update:
        covered = update.get("covered", [])
        not_covered = update.get("not_covered", [])
        case["partial_coverage"] = {
            "is_partial": case["status"] == "BLOCKED" and bool(covered),
            "covered": covered,
            "not_covered": not_covered,
        }
    if "remaining_actions" in update:
        case["remaining_actions"] = update["remaining_actions"]


def module_counts(cases: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Aggregate final statuses by formal module."""

    grouped: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        grouped[case["module"]][case["status"]] += 1
        grouped[case["module"]]["TOTAL"] += 1
    ordered: dict[str, dict[str, int]] = {}
    for module in ("MCP", "ENV", "REC", "MET", "LIFE", "CALC", "DB"):
        ordered[module] = {
            status: grouped[module].get(status, 0)
            for status in ("PASS", "FAIL", "BLOCKED", "EXCLUDED", "TOTAL")
        }
    return ordered


def markdown_report(report: dict[str, Any]) -> str:
    """Render the final coverage report in Chinese Markdown."""

    status_counts = report["summary"]["status_counts"]
    lines = [
        "# Factor 4.0 最终覆盖汇总",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        "- 环境：`test`",
        "- 模式：离线证据合并；本次未请求 MCP、HTTP 或数据库",
        "- 正式基线：`100` 个 case；HMAC-001~007 仍作为非正式门禁依赖，不计入 100",
        "- 判定规则：必需分支仍被阻断时，即使其余断言通过，正式 case 仍为 `BLOCKED`",
        "",
        "## 最终状态",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    for status in ("PASS", "FAIL", "BLOCKED", "EXCLUDED"):
        lines.append(f"| {status} | {status_counts[status]} |")
    lines.extend(["", "| 模块 | PASS | FAIL | BLOCKED | EXCLUDED | 合计 |", "|---|---:|---:|---:|---:|---:|"])
    for module, counts in report["summary"]["module_counts"].items():
        lines.append(
            f"| {module} | {counts['PASS']} | {counts['FAIL']} | {counts['BLOCKED']} | "
            f"{counts['EXCLUDED']} | {counts['TOTAL']} |"
        )

    lines.extend(["", "## 本轮状态调整", ""])
    if report["status_changes"]:
        lines.extend(["| Case | 旧状态 | 最终状态 | 依据 |", "|---|---|---|---|"])
        for change in report["status_changes"]:
            lines.append(
                f"| `{change['case_id']}` | `{change['from']}` | `{change['to']}` | {change['reason']} |"
            )
    else:
        lines.append("指定报告中没有可依据明确 formal verdict 改写状态的 case；仅更新了分类、说明和证据。")

    lines.extend(["", "## 独立缺陷组", "", "DB-605 只是 ENV-108 与 MET-310 的汇总，不重复计根因；CALC-510 含两个独立公式缺陷族。DB-613 已由同一只读事务内的两次稳定数据库读取确认。", "", "| 缺陷组 | Cases | 严重度 | 当前证据状态 | 已确认事实 | 根因边界 |", "|---|---|---|---|---|---|"])
    for defect in report["independent_defect_groups"]:
        lines.append(
            f"| `{defect['defect_id']}` | {', '.join(defect['formal_case_ids'])} | `{defect['severity']}` | "
            f"`{defect.get('current_evidence_status', 'CONFIRMED')}` | {defect['confirmed_fact']} | {defect['root_cause_status']} |"
        )

    lines.extend(["", "## 仍需外部前置", "", "下列是覆盖阻断条件，不计产品缺陷。", "", "| 前置组 | Case | 所需条件 |", "|---|---|---|"])
    for item in report["remaining_external_prerequisites"]:
        lines.append(
            f"| `{item['prerequisite_id']}` | {', '.join(item['case_ids'])} | {item['description']} |"
        )

    lines.extend(["", "## 汇总裁决缺口", "", "这些 case 不是缺少外部服务条件，而是专项报告没有给出新的 aggregate formal verdict。", "", "| 缺口 | Case | 说明 |", "|---|---|---|"])
    for item in report["adjudication_gaps"]:
        lines.append(f"| `{item['gap_id']}` | {', '.join(item['case_ids'])} | {item['description']} |")

    lines.extend(["", "## 已替代报告", "", "| 旧报告 | 替代依据 | 原因 |", "|---|---|---|"])
    for item in report["superseded_reports"]:
        lines.append(f"| `{item['path']}` | `{item['superseded_by']}` | {item['reason']} |")

    lines.extend(["", "## 全量逐项状态", "", "| Case | 模块 | 标题 | 状态 | 分类 | 最终说明 |", "|---|---|---|---|---|---|"])
    for case in report["cases"]:
        reason = str(case["reason"]).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| `{case['case_id']}` | {case['module']} | {case['title']} | `{case['status']}` | "
            f"`{case['classification']}` | {reason} |"
        )

    lines.extend(
        [
            "",
            "## 排除范围",
            "",
            *[f"- {item}" for item in report["excluded_findings"]],
            "",
            "## 校验",
            "",
            f"- Case ID 唯一且与 100 项基线完全一致：`{report['verification']['exact_baseline_case_set']}`",
            f"- 状态合计为 100：`{report['verification']['status_sum_is_100']}`",
            f"- 指定权威证据全部存在并记录 SHA-256：`{report['verification']['all_authoritative_sources_exist']}`",
            f"- 输出敏感模式扫描：`{report['verification']['sensitive_scan_passed']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Build, validate, and write the final offline coverage artifacts."""

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_cases = baseline["cases"]
    baseline_ids = [case["case_id"] for case in baseline_cases]
    if len(baseline_ids) != 100 or len(set(baseline_ids)) != 100:
        raise RuntimeError("Coverage baseline is not exactly 100 unique formal cases")

    sources = deepcopy(SOURCE_REPORTS)
    for source in sources:
        absolute = ROOT / source["path"]
        source["exists"] = absolute.is_file()
        if not source["exists"]:
            raise FileNotFoundError(absolute)
        source["sha256"] = sha256_file(absolute)

    mapped_sources: defaultdict[str, list[str]] = defaultdict(list)
    for source in sources:
        for case_id in source["formal_case_ids"]:
            mapped_sources[case_id].append(source["path"])

    cases = deepcopy(baseline_cases)
    for case in cases:
        case_id = case["case_id"]
        case["baseline_status"] = case["status"]
        if case_id in FORMAL_UPDATE_IDS:
            apply_update(case, UPDATES[case_id])
        case["status_changed_since_baseline"] = case["status"] != case["baseline_status"]
        case["adjudication_sources"] = mapped_sources.get(case_id, []) or [str(BASELINE_PATH.relative_to(ROOT))]
        case["evidence_paths"] = list(
            dict.fromkeys([*case.get("evidence_paths", []), *mapped_sources.get(case_id, [])])
        )

    case_ids = [case["case_id"] for case in cases]
    invalid_statuses = sorted({case["status"] for case in cases} - ALLOWED_STATUSES)
    if invalid_statuses:
        raise RuntimeError(f"Unexpected final statuses: {invalid_statuses}")
    status_counts = Counter(case["status"] for case in cases)
    if sum(status_counts.values()) != 100:
        raise RuntimeError("Final status count does not sum to 100")

    status_case_ids = {
        status: [case["case_id"] for case in cases if case["status"] == status]
        for status in ("PASS", "FAIL", "BLOCKED", "EXCLUDED")
    }
    blocked_ids = set(status_case_ids["BLOCKED"])
    prerequisite_ids = {
        case_id for item in EXTERNAL_PREREQUISITES for case_id in item["case_ids"]
    }
    adjudication_gap_ids = {
        case_id for item in ADJUDICATION_GAPS for case_id in item["case_ids"]
    }
    uncovered_blocked_ids = sorted(blocked_ids - prerequisite_ids - adjudication_gap_ids)
    if uncovered_blocked_ids:
        raise RuntimeError(f"Blocked cases missing prerequisite grouping: {uncovered_blocked_ids}")

    generated_at = datetime.now(SHANGHAI)
    report: dict[str, Any] = {
        "authority": "This is the final offline merge of the 100-case coverage baseline and the listed authoritative closure reports.",
        "generated_at": generated_at.isoformat(),
        "environment": "test",
        "mode": "OFFLINE_EVIDENCE_MERGE",
        "external_requests_made_in_this_merge": 0,
        "database_queries_made_in_this_merge": 0,
        "database_writes_made_in_this_merge": 0,
        "baseline": {
            "path": str(BASELINE_PATH.relative_to(ROOT)),
            "formal_case_count": 100,
            "sha256": sha256_file(BASELINE_PATH),
            "note": "The baseline supplies the exact formal case set; later case-specific evidence has precedence.",
        },
        "adjudication_rules": [
            "Later trustworthy case-specific evidence supersedes earlier aggregate status.",
            "A formal case is PASS only when every material applicable assertion has evidence.",
            "If a required branch remains blocked, partial successes do not promote the formal case above BLOCKED.",
            "NOT_APPLICABLE branches do not block a case when the deployed contract explicitly excludes that capability.",
            "TS or CS validity is sufficient under any_valid_scope; both dimensions are not required.",
            "Derived case DB-605 does not create a new defect root beyond ENV-108 and MET-310.",
            "User-directed exclusions remain EXCLUDED and are not counted as product defects.",
        ],
        "authoritative_sources": sources,
        "summary": {
            "formal_case_count": 100,
            "status_counts": {status: status_counts.get(status, 0) for status in ("PASS", "FAIL", "BLOCKED", "EXCLUDED")},
            "module_counts": module_counts(cases),
            "formal_failure_case_count": len(status_case_ids["FAIL"]),
            "formal_independent_defect_group_count": len(DEFECT_GROUPS),
            "currently_confirmed_defect_group_count": sum(
                defect.get("current_evidence_status", "CONFIRMED") == "CONFIRMED"
                for defect in DEFECT_GROUPS
            ),
            "status_changed_since_baseline_count": sum(case["status_changed_since_baseline"] for case in cases),
        },
        "status_case_ids": status_case_ids,
        "status_changes": [
            {
                "case_id": case["case_id"],
                "from": case["baseline_status"],
                "to": case["status"],
                "reason": "Explicit formal case verdict in a listed authoritative report.",
            }
            for case in cases
            if case["status_changed_since_baseline"]
        ],
        "cases": cases,
        "independent_defect_groups": deepcopy(DEFECT_GROUPS),
        "derived_failure_cases": [
            {
                "case_id": "DB-605",
                "upstream_case_ids": ["ENV-108", "MET-310"],
                "counts_as_independent_defect": False,
            }
        ],
        "remaining_external_prerequisites": deepcopy(EXTERNAL_PREREQUISITES),
        "adjudication_gaps": deepcopy(ADJUDICATION_GAPS),
        "superseded_reports": deepcopy(SUPERSEDED_REPORTS),
        "evidence_conflicts": [],
        "resolved_evidence_conflicts": [
            {
                "case_id": "DB-613",
                "baseline_verdict": "FAIL: environment_status.WIDE_RANGE.route_count=0 while 86 matching routes existed",
                "later_observation": "The 12:48 tool-matrix response for the same batch/version reports route_count=86",
                "resolution": (
                    "A dedicated read-only stable-snapshot revalidation read the stored summary and exact routes twice: "
                    "both reads remained 0 versus 86. The MCP observation is therefore a dynamic response and does "
                    "not establish that the persisted published summary was repaired."
                ),
                "resolution_evidence_path": SOURCE_REPORTS[9]["path"],
                "later_evidence_path": "reports/factor4-resume/20260904T124800+0800-tool-matrix-pit/010-MATRIX-factor_get_environment_metrics-CURRENT.response.json",
            }
        ],
        "excluded_findings": baseline["excluded_findings"],
        "verification": {
            "case_id_count": len(case_ids),
            "unique_case_id_count": len(set(case_ids)),
            "exact_baseline_case_set": case_ids == baseline_ids,
            "status_sum_is_100": sum(status_counts.values()) == 100,
            "all_authoritative_sources_exist": all(source["exists"] for source in sources),
            "all_blocked_cases_have_prerequisite_group": not uncovered_blocked_ids,
            "uncovered_blocked_case_ids": uncovered_blocked_ids,
            "sensitive_scan_passed": False,
        },
    }

    report["verification"]["sensitive_scan_passed"] = True
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_text = markdown_report(report)
    sensitive_hits = {
        "mcp_token": bool(TOKEN_PATTERN.search(json_text) or TOKEN_PATTERN.search(markdown_text)),
        "jwt": bool(JWT_PATTERN.search(json_text) or JWT_PATTERN.search(markdown_text)),
    }
    if any(sensitive_hits.values()):
        raise RuntimeError(f"Sensitive pattern found in final report: {sensitive_hits}")

    output = REPORT_ROOT / f"{generated_at.strftime('%Y%m%dT%H%M%S%z')}-final-coverage"
    output.mkdir(parents=True, exist_ok=False)
    (output / "coverage.json").write_text(json_text, encoding="utf-8")
    (output / "coverage.md").write_text(markdown_text, encoding="utf-8")
    print(output)
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
