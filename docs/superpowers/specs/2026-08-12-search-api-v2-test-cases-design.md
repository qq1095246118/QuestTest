# 搜索接口 V2 测试用例设计说明

## 1. 目标

本轮只补充搜索接口测试用例设计，不编写自动化代码。目标是：

1. 为新增的 `GET /api/v1/events/recall` 建立可独立执行的接口用例。
2. 根据新版接口文档更新受影响的旧接口回归用例。
3. 先验证接口自身的入参约束、响应结构和业务关系，再验证接口返回与只读数据库的一致性。
4. 不使用当前环境探测到的固定新闻 ID、数量、日期或响应内容倒推正确结果。
5. 用例以业务规则、接口数据关系和数据库一致性为主；兼容性、性能和纯格式规范不扩展为独立用例大类。

## 2. 事实与边界

### 2.1 已确认事实

- 新版文档新增并描述了 `GET /api/v1/events/recall`。
- 新版文档修改了 `POST /api/v1/search` 的排序和 ES `doc` 默认字段。
- 新版文档把 `/search` 和 `/search/ranked` 的默认 ES `doc` 字段扩展为 7 个，包含 `title`。
- 当前实时 OpenAPI 仍保留旧的 6 字段描述，不能作为本轮唯一契约来源。
- 事件召回响应包含 `matched_by`、`total_candidates`、`filtered_noise`、`occurrences` 和 `events` 等字段。
- 事件召回使用 `btc_impact_tag` 作为标签数据来源，普通新闻数据可在 `news_information` 中核对。
- 事件召回可能返回大于等于 `9000000000` 的 Redis 命名空间新闻 ID；这类 ID 不一定存在于 `news_information`。

### 2.2 本轮覆盖

新增接口：

- `GET /api/v1/events/recall`

旧接口回归：

- `POST /api/v1/search`
- `GET /api/v1/search/web`
- `POST /api/v1/search/ranked`
- `POST /api/v1/search/smart`

### 2.3 本轮不覆盖

- 不新增或设计 `POST /api/v1/smart_search` 的业务用例。新版 PDF 虽然包含该接口，但当前任务已经明确新接口只做 `events/recall`。
- 不覆盖用户身份鉴权、HTTP 方法错误、Content-Type 错误、限流、性能、并发、外部搜索服务故障注入、ES/LLM 故障注入。
- 不覆盖浏览器、客户端版本、字符集兼容等兼容性矩阵。
- 不通过数据库写入、修改或清理测试数据。
- 不把当前环境中某个固定关键词的固定返回数量写成稳定预期。

## 3. 测试用例组织方式

继续使用现有 `docs/search_api_test_cases.md`，不新建第二份业务用例文档。

每条用例保持独立，并按以下顺序书写：

1. 用例目的。
2. 完整请求方式、请求头和参数。
3. 接口自身断言，按序号逐条列出。
4. 必要时给出使用本次响应实际 ID 的 SQL。
5. DB 断言及无法对账时的明确边界。

接口自身断言始终先执行。DB 查询只读，用于验证接口返回是否与持久化数据一致，不替代接口本身的字段、类型、排序和业务逻辑断言。

## 4. `GET /api/v1/events/recall` 设计

### 4.1 参数契约

| 参数 | 类型 | 必填 | 约束 | 默认值 |
|---|---|---:|---|---|
| `keyword` | string | 是 | `min_length=1` | 无 |
| `gap_days` | int | 否 | `1 <= n <= 90` | `7` |
| `limit` | int | 否 | `1 <= n <= 200` | `50` |
| `window_days` | int | 否 | `n >= 1` | `null` |
| `exclude_promo` | bool | 否 | 布尔值 | `true` |

中文关键词必须通过 URL 编码方式传递。用例中的请求应使用客户端的 query 参数编码能力，不能手工拼接未编码的中文 URL。

### 4.2 成功响应的共同断言

所有成功用例都先断言：

1. HTTP 状态码为 `200`。
2. 根节点为 JSON 对象，`code=0`、`message="ok"`、`data` 为对象。
3. `data.keyword` 与请求中的关键词一致。
4. `data.matched_by` 只能是 `tag`、`title` 或 `null`。
5. `data.gap_days` 等于请求值或文档默认值。
6. `total_candidates`、`filtered_noise`、`occurrences` 为非负整数。
7. `events` 为数组，`occurrences` 表示截断前的聚类事件总数，`len(events) = min(occurrences, limit)`。
8. 每个事件的 `news_id`、`doc_id`、`title`、`publish_time`、`impact_events` 类型符合文档。
9. `doc_id` 等于 `news:` 加上字符串形式的 `news_id`。
10. 有事件时按 `publish_time` 倒序；无命中时 `matched_by=null` 且 `events=[]`。
11. `occurrences <= total_candidates`，返回的 `news_id` 和 `doc_id` 均不重复。

### 4.3 业务与接口用例矩阵

新增 `E01` 至 `E15`，重点验证事件召回的业务分支和字段关系：

| 编号 | 场景 | 重点 |
|---|---|---|
| E01 | 省略可选参数，使用默认值 | 验证默认 `gap_days=7`、`limit=50`、`exclude_promo=true` |
| E02 | 标签命中优先 | 验证有标签命中时 `matched_by=tag`，不退回标题路径 |
| E03 | 无标签时标题兜底 | 验证标题分词匹配并返回 `matched_by=title` |
| E04 | 完全无命中 | 验证 `matched_by=null`、`events=[]` 及计数字段关系 |
| E05 | 调小和调大 `gap_days` | 对同一关键词分别请求较小、默认、较大间隔；候选数保持不变，事件数随间隔增大不得增加 |
| E06 | 聚类间隔边界 | 通过数据库候选时间线验证相邻间隔 `<= gap_days` 合并、`> gap_days` 才拆成新事件 |
| E07 | 每组保留最早新闻 | 重建可追溯候选组，验证接口代表新闻是组内发布时间最早的一条 |
| E08 | 最终按时间倒序 | 验证接口先选择每组最早新闻，再把各组代表按时间倒序返回 |
| E09 | `limit` 只截断列表 | 对同一条件比较 `limit=1` 与大 limit；`total_candidates`、`occurrences` 不变，仅 `events` 被截断且保留最近事件 |
| E10 | `window_days` 时间窗 | 与全历史结果比较，验证时间窗内候选和事件均为全历史结果的受限集合 |
| E11 | 时间窗边界和时区 | 按请求发起时间计算起点，验证返回时间不早于边界；区分新闻发布时间与标签 UTC 写入时间 |
| E12 | 默认促销过滤 | 验证 `exclude_promo=true` 时结果标题不包含文档定义的促销词，并核对过滤数量关系 |
| E13 | 关闭促销过滤 | 与默认过滤使用同一关键词；关闭后 `filtered_noise=0`，原始候选数等于过滤后候选数加被过滤数 |
| E14 | 计数守恒与唯一性 | 验证候选数、聚类事件数、limit 截断后数组长度以及事件 ID 去重关系 |
| E15 | 全部可选参数组合 | 同时传入 `gap_days`、`limit`、`window_days`、`exclude_promo`，验证各参数都作用于对应阶段且互不覆盖 |

E05 只断言同一批候选数据上的单调关系，不写死经验表中的事件数量。E06、E07 只有在数据库能够完整还原该次匹配候选时才做全量聚类对账；若存在数据库无法还原的 ES 候选，必须明确报告数据源缺口，不能将近似 SQL 的结果当作正确预期。

### 4.4 数据一致性用例

E16 至 E21 专门验证接口返回与数据库的可追溯关系：

- E16：使用响应事件的 `news_id` 查询 `btc_impact_tag`，逐条核对接口 `impact_events` 与表字段一致。
- E17：核对 `btc_impact_tag.impact_events` 与 `result_json.impact_events` 内部一致，避免接口读取到过期或不一致的标签快照。
- E18：使用 `LEFT JOIN news_information` 核对返回事件的标题和发布时间；有 DB 行时必须一致，无 DB 行时明确标记为命名空间数据源缺口。
- E19：对标签命中关键词从 `btc_impact_tag.impact_events` 反查候选，验证 `matched_by=tag` 的数据依据，并确认标签路径优先于标题兜底。
- E20：同时覆盖普通 ID 与 `news_id >= 9000000000` 的命名空间 ID；两类 ID 都核对 `doc_id` 和标签，新闻主表只在实际存在记录时核对。
- E21：核对 `gap_days`、`limit`、`window_days`、`exclude_promo` 变化前后的字段不变量和变量，防止参数被接受但未真正参与业务计算。

普通新闻的 SQL 采用响应 ID 参数化，不把 ID 拼接到 SQL 字符串中。示例结构如下，实际执行时通过数据库驱动绑定 JSON 数组参数：

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

标签核对 SQL 同样使用本次响应的实际 ID，并通过 `JSON_TABLE` 参数化：

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
    t.impacted_dimensions,
    t.core_dimensions,
    t.all_dimensions,
    t.direction,
    t.confidence,
    t.degraded,
    t.updated_at
FROM requested_ids r
LEFT JOIN `perception-test`.btc_impact_tag t
    ON t.news_id = r.news_id
ORDER BY r.news_id;
```

数据库驱动将本次响应 ID 序列化为 JSON 字符串后绑定到 `%s`，不能拼接 ID。

对于 `news_id >= 9000000000`：

- 必须核对接口自身的 ID、标题、时间和 `doc_id` 关系。
- 优先查询 `btc_impact_tag` 进行标签对账。
- 不要求 `news_information` 必须存在对应行，因为这类 ID 可能来自 Redis 命名空间。

### 4.5 必要的接口参数用例

只保留直接关系到接口可用性的必要校验，不扩展客户端兼容和格式规范矩阵。新增 `E22` 至 `E30`：

| 编号 | 输入 | 预期 |
|---|---|---|
| E22 | 缺少 `keyword` | HTTP 422，错误定位 `keyword` |
| E23 | `keyword=""` | HTTP 422，违反最小长度 |
| E24 | `gap_days=0` | HTTP 422，低于业务下限 |
| E25 | `gap_days=91` | HTTP 422，超过业务上限 |
| E26 | `limit=0` | HTTP 422，低于业务下限 |
| E27 | `limit=201` | HTTP 422，超过业务上限 |
| E28 | `window_days=0` | HTTP 422，时间窗必须至少为 1 天 |
| E29 | 整数参数传不可解析值 | 分别验证 `gap_days`、`limit`、`window_days` 不会静默回退默认值 |
| E30 | `exclude_promo` 传不可解析值 | HTTP 422，不能静默按默认 `true` 执行 |

GET query 参数在传输层都是文本，因此不设计“`keyword` 为整数或对象”的伪类型场景；`keyword=123` 对接口而言是合法字符串关键词。E29 中各整数参数作为独立操作和结果书写，但归为同一个接口参数解析场景，不重复扩展相同断言。

负向用例统一要求：

1. 不返回 HTTP 500。
2. 错误响应能定位具体参数，使用统一错误信封时校验 `code=422`，使用 FastAPI 标准错误时校验 `detail` 中的 `loc`、`msg`、`type`。
3. 不返回成功结构中的 `events`、`occurrences` 或伪造的业务结果。
4. 不做全表数量差验证，因为参数校验发生在业务执行前，且测试环境可能有并发请求。

## 5. 旧接口回归设计

### 5.1 `/search` 和 `/search/ranked` 默认字段

将现有 S03、S06、R02、R05 中的默认字段断言由 6 个更新为以下 7 个：

```text
title
doc_hash_id
doc_type
source_url
content
publish_time
created_at
```

自定义 `fields` 用例仍要求 `doc` 键集合严格等于请求字段集合。顶层排序和来源字段不受 `fields` 裁剪影响。

### 5.2 `/search` 排序

将旧的“按 `score` 或相关性乘时间排序”断言更新为：

```text
rank_score = keyword_hits * 1000 + freshness_score
```

断言内容：

1. 每条结果包含数值型 `rank_score`、`keyword_hits`、`freshness_score` 和 `freshness_rule`。
2. `rank_score` 与两个组成字段按允许的浮点精度一致。
3. `results` 按 `rank_score` 降序。
4. `rank_score` 相同时按 `publish_time` 降序。
5. `normal` 与 `realtime` 分路结构仍与请求的 `include_web` 语义一致。

`freshness_score` 的精确值依赖后端热加载的规则文件和标签属性。若测试工程无法读取同一版本的规则文件，不用本地重新计算固定数值；至少校验字段类型、公式关系和排序关系，并把规则文件版本作为执行环境信息记录。

### 5.3 `/search/ranked` 的文档内部冲突

新版 PDF 的参数和字段说明要求 7 个默认字段，但响应说明部分仍出现 6 个字段；排序说明也没有完全统一使用 `rank_score` 还是原 `score`。因此：

- 文档用例中明确记录该冲突。
- 参数表和默认字段清单按 7 字段设计。
- 在后端或接口负责人确认前，不把“响应必须是 6 个字段”写成稳定正确预期。
- 排序用例同时采集 `score` 和 `rank_score` 的实际响应结构；若接口只返回其中一个，记录为契约差异，不用“任意一个存在”作为自动化通过条件。

### 5.4 `doc_type` 映射

当前数据库存在 `ARTICLE -> news`、`ANNOUNCEMENT -> exchange_announcement`、`NEWSFLASH -> newsflash` 等映射，不能继续用 `LOWER(news_information.data_type)` 作为普遍正确规则。映射表或后端规范明确前：

- 继续核对 `doc_id`、标题、内容、来源 URL、时间、哈希等可直接对应字段。
- `doc_type` 只记录实际值和待确认映射，不把简单小写结果作为稳定断言。

## 6. 文档修改结果

完成后 `docs/search_api_test_cases.md` 应包含：

- 原有 76 条旧接口用例，更新受影响的字段、排序和 DB 规则；不额外增加性能、兼容性或纯规范回归用例。
- `E01-E30` 事件召回用例，其中 E01-E21 为业务、接口成功和数据一致性场景，E22-E30 为必要参数场景。
- 统一记录当前 OpenAPI 落后于新版 PDF 的事实。
- 不包含 `smart_search` 新接口业务用例，不包含自动化 Python 代码。

## 7. 完成标准

设计文档和业务用例文档均满足：

1. 每条用例请求参数完整、断言编号清晰、SQL 与用例放在一起。
2. 接口自身断言与 DB 对账断言分开，且接口断言优先。
3. 没有固定环境返回值倒推的预期。
4. 普通新闻 ID 和 Redis 命名空间 ID采用不同 DB 对账路径。
5. 旧接口受新版文档影响的断言全部可定位、可回归。
6. 未确认的文档冲突不会被静默覆盖。
