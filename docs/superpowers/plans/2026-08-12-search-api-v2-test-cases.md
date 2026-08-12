# 搜索接口 V2 测试用例文档更新计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根据新版搜索接口文档更新旧接口回归用例，并在现有文档中补充以业务、接口和数据库一致性为重点的 `GET /api/v1/events/recall` 用例。

**Architecture:** 仅修改 API 自动化文档。`docs/search_api_test_cases.md` 继续作为唯一业务用例文档；设计说明负责记录边界和冲突，业务文档中的每条用例直接包含完整请求、编号化接口断言、必要 SQL 和 DB 断言。

**Tech Stack:** Markdown、HTTP JSON API、MySQL 8 `JSON_TABLE`、只读数据核对。

---

## 文件范围

- Modify: `docs/search_api_test_cases.md`
- Reference: `docs/superpowers/specs/2026-08-12-search-api-v2-test-cases-design.md`
- Reference: `/Users/wrh/Downloads/搜索接口文档2-仅用作测试使用.pdf`

不修改 Python 自动化代码、`service/`、环境配置或数据库数据。

### Task 1: 更新文档范围和通用数据库规则

**Files:**
- Modify: `docs/search_api_test_cases.md:1-140`

- [ ] **Step 1: 更新接口范围和设计依据**

把 `GET /api/v1/events/recall` 加入接口范围，把设计依据更新为新版 PDF、2026-08-12 OpenAPI 和数据库实际表结构，并明确不包含 `POST /api/v1/smart_search`。

- [ ] **Step 2: 修正新闻表回源规则**

从通用 SQL 删除 `LOWER(ni.data_type) AS expected_doc_type`，删除 `doc_type=LOWER(data_type)` 的错误映射断言。保留 `doc_id`、标题、正文、URL、哈希和时间字段核对，并将 `doc_type` 标记为待后端提供正式映射。

- [ ] **Step 3: 增加事件召回通用对账规则**

加入两个参数化 SQL：

```sql
WITH requested_ids AS (
    SELECT jt.news_id
    FROM JSON_TABLE(
        %s,
        '$[*]' COLUMNS (news_id BIGINT PATH '$')
    ) AS jt
)
SELECT
    r.news_id AS requested_news_id,
    CASE WHEN ni.id IS NULL THEN 0 ELSE 1 END AS db_row_found,
    ni.id,
    ni.title,
    ni.published_at,
    ni.source_url,
    ni.content_hash
FROM requested_ids r
LEFT JOIN `perception-test`.news_information ni
    ON ni.id = r.news_id
ORDER BY r.news_id;
```

```sql
WITH requested_ids AS (
    SELECT jt.news_id
    FROM JSON_TABLE(
        %s,
        '$[*]' COLUMNS (news_id BIGINT PATH '$')
    ) AS jt
)
SELECT
    r.news_id AS requested_news_id,
    CASE WHEN t.news_id IS NULL THEN 0 ELSE 1 END AS tag_row_found,
    t.impact_events,
    JSON_EXTRACT(t.result_json, '$.impact_events') AS snapshot_impact_events,
    t.degraded,
    t.updated_at
FROM requested_ids r
LEFT JOIN `perception-test`.btc_impact_tag t
    ON t.news_id = r.news_id
ORDER BY r.news_id;
```

- [ ] **Step 4: 检查通用规则**

Run:

```bash
rg -n 'events/recall|LOWER\(|expected_doc_type|btc_impact_tag|JSON_TABLE' docs/search_api_test_cases.md
```

Expected: 存在事件召回和两个表的参数化 SQL；通用规则中不存在 `LOWER(data_type)` 映射断言。

### Task 2: 更新旧接口回归契约

**Files:**
- Modify: `docs/search_api_test_cases.md:140-3020`

- [ ] **Step 1: 更新 `/search` 默认字段**

将 S03、S06 和相关说明由 6 字段改为以下 7 字段：

```text
title, doc_hash_id, doc_type, source_url, content, publish_time, created_at
```

- [ ] **Step 2: 更新 `/search` 排序和字段断言**

将旧 `score` 排序断言改为：

```text
rank_score = keyword_hits * 1000 + freshness_score
```

逐条验证 `rank_score`、`keyword_hits`、`freshness_score`、`freshness_rule`、降序关系和同分时 `publish_time` 倒序。

- [ ] **Step 3: 更新 `/search/ranked` 默认字段并保留冲突说明**

将 R02、R05 的默认字段改为 7 个，同时保留新版 PDF 响应示例仍写 6 字段的契约冲突。排序字段冲突不写成“任意一个存在即可通过”。

- [ ] **Step 4: 回归 `/search/web` 和 `/search/smart`**

核对新版 PDF 后，只更新版本依据和仍有效的契约说明；没有明确变化的请求、响应和 DB 会话核对用例保持不变，不新增性能、兼容性或规范用例。

- [ ] **Step 5: 扫描旧口径残留**

Run:

```bash
rg -n '六个|六字段|6 个|6个|LOWER\(|expected_doc_type|按 `score` 降序' docs/search_api_test_cases.md
```

Expected: 不存在被新版契约替代的 6 字段和简单小写映射断言；仅允许在“文档内部冲突”说明中出现 6 字段文本。

### Task 3: 追加事件召回业务与数据用例

**Files:**
- Modify: `docs/search_api_test_cases.md`

- [ ] **Step 1: 增加 `GET /api/v1/events/recall` 章节**

新增 E01-E15，覆盖默认参数、标签优先、标题兜底、无命中、gap 单调关系、聚类边界、代表新闻、倒序、limit 截断、时间窗、促销过滤、计数守恒和组合参数。

- [ ] **Step 2: 增加 E16-E21 数据一致性用例**

每条用例写明完整 GET 参数、接口断言、使用响应实际 ID 的 SQL 和 DB 断言。覆盖：

```text
接口 impact_events = btc_impact_tag.impact_events
btc_impact_tag.impact_events = result_json.impact_events
接口 title/publish_time = news_information.title/published_at
matched_by=tag 有标签数据依据
普通 ID 与 >=9000000000 命名空间 ID 的不同核对路径
参数变化前后的字段不变量和变量
```

- [ ] **Step 3: 增加 E22-E30 必要参数用例**

只覆盖必填、空值、数值上下界和不可解析参数。GET query 参数没有 JSON 对象类型，因此不设计伪造的 `keyword` 对象类型场景。

- [ ] **Step 4: 校验 limit 和 occurrences 语义**

确保所有事件召回用例统一使用：

```text
len(events) = min(occurrences, limit)
```

`limit` 只截断 `events`，不修改 `total_candidates` 或 `occurrences`。

### Task 4: 文档完整性验证

**Files:**
- Verify: `docs/search_api_test_cases.md`

- [ ] **Step 1: 校验章节和编号**

Run:

```bash
rg -n '^## |^### (S|W|R|SM|E)[0-9]+' docs/search_api_test_cases.md
```

Expected: 旧接口编号无重复；事件召回编号从 E01 连续到 E30。

- [ ] **Step 2: 校验每条事件用例结构**

Run:

```bash
rg -c '^### E[0-9]{2} ' docs/search_api_test_cases.md
rg -c -- '- 接口断言：' docs/search_api_test_cases.md
```

Expected: 第一条输出 `30`；每条 E 用例均有编号化接口断言，需要 DB 对账的用例包含 SQL 和 DB 断言。

- [ ] **Step 3: 校验 SQL 和 Markdown**

Run:

```bash
git diff --check -- docs/search_api_test_cases.md
rg -n 'IN \(\.\.\.|你的|TODO|TBD|固定预期' docs/search_api_test_cases.md
```

Expected: `git diff --check` 无输出；不存在不可执行 SQL 占位、TODO 或固定环境结果预期。

- [ ] **Step 4: 查看最终差异**

Run:

```bash
git diff --stat -- docs/search_api_test_cases.md
git diff -- docs/search_api_test_cases.md
```

Expected: 只修改搜索接口测试用例文档，内容与设计说明一致。
