# 搜索接口 V2 测试用例设计说明

## 1. 目标

本轮只补充搜索接口测试用例设计，不编写自动化代码。目标是：

1. 为新增的 `GET /api/v1/events/recall` 建立可独立执行的接口用例。
2. 根据新版接口文档更新受影响的旧接口回归用例。
3. 先验证接口自身的入参约束、响应结构和业务关系，再验证接口返回与只读数据库的一致性。
4. 不使用当前环境探测到的固定新闻 ID、数量、日期或响应内容倒推正确结果。

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
7. `events` 为数组，数组长度等于 `occurrences`，且不超过 `limit`。
8. 每个事件的 `news_id`、`doc_id`、`title`、`publish_time`、`impact_events` 类型符合文档。
9. `doc_id` 等于 `news:` 加上字符串形式的 `news_id`。
10. 有事件时按 `publish_time` 倒序；无命中时 `matched_by=null` 且 `events=[]`。

### 4.3 正向用例矩阵

新增 `E01` 至 `E16`，每条用例独立执行：

| 编号 | 场景 | 重点 |
|---|---|---|
| E01 | 省略可选参数，使用默认值 | 验证默认 `gap_days=7`、`limit=50`、`exclude_promo=true` |
| E02 | 标签命中优先 | 验证有标签命中时 `matched_by=tag`，不退回标题路径 |
| E03 | 无标签时标题兜底 | 验证标题分词匹配并返回 `matched_by=title` |
| E04 | 完全无命中 | 验证 `matched_by=null`、`events=[]` 及计数字段关系 |
| E05 | `gap_days=1` | 验证最小合法间隔被接受，并按间隔聚类 |
| E06 | `gap_days=90` | 验证最大合法间隔被接受，聚类不会超过接口上限逻辑 |
| E07 | 调小 `gap_days` | 同一关键词使用更小间隔时，验证事件拆分关系，而不是固定数量 |
| E08 | 调大 `gap_days` | 同一关键词使用更大间隔时，验证事件合并关系，而不是固定数量 |
| E09 | `limit=1` | 验证最多返回一条、`occurrences <= 1` |
| E10 | `limit=200` | 验证最大合法返回上限被接受 |
| E11 | `window_days` 时间窗 | 验证事件均落在最近 N 天窗口内 |
| E12 | 默认促销过滤 | 验证 `exclude_promo=true` 时结果标题不包含文档定义的促销词，并记录 `filtered_noise` |
| E13 | 关闭促销过滤 | 验证 `exclude_promo=false` 不启用该过滤，不能强制要求一定出现促销结果 |
| E14 | 显式传入默认值 | 验证显式传 `gap_days=7`、`limit=50`、`exclude_promo=true` 与省略参数的语义一致 |
| E15 | 全部可选参数同时传入 | 验证参数组合不会互相覆盖，响应回显有效参数 |
| E16 | 聚类代表和顺序 | 验证每组只保留最早新闻，最终事件按时间倒序返回 |

E07、E08 只断言同一数据集在参数变化下的单调关系：通常 `gap_days` 越小，事件拆分不会减少；越大，事件合并不会增加。若数据边界或时间相等导致无法证明关系，应以结构和聚类规则断言，不把经验表中的数量写死。

### 4.4 数据一致性用例

E17 至 E19 专门验证接口返回与数据库的可追溯关系：

- E17：使用响应事件的 `news_id` 查询 `btc_impact_tag`，逐条核对 `impact_events`，并验证标签命中事件的 `matched_by` 与标签数据存在关系。
- E18：对普通新闻 ID 使用 `LEFT JOIN` 查询 `news_information`，核对标题和发布时间；接口 ID 在本地新闻表不存在时，不把该情况误判为接口结构错误。
- E19：验证 `window_days` 的时间边界、候选数、去重数和最终事件数组的关系；时间边界按接口返回的 `publish_time` 与请求执行时间统一时区计算。

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

标签核对 SQL 使用本次响应的实际 ID：

```sql
SELECT
    news_id,
    impact_events,
    impacted_dimensions,
    core_dimensions,
    all_dimensions,
    direction,
    confidence,
    degraded,
    updated_at
FROM `perception-test`.btc_impact_tag
WHERE news_id IN (...本次响应中的实际 news_id...)
ORDER BY news_id;
```

自动化实现时 `IN (...)` 也必须改成驱动占位符，以上文档中的省略号只表示设计阶段的 SQL 形态，不是可直接执行的拼接方式。

对于 `news_id >= 9000000000`：

- 必须核对接口自身的 ID、标题、时间和 `doc_id` 关系。
- 优先查询 `btc_impact_tag` 进行标签对账。
- 不要求 `news_information` 必须存在对应行，因为这类 ID 可能来自 Redis 命名空间。

### 4.5 负向用例矩阵

新增 `E20` 至 `E31`：

| 编号 | 输入 | 预期 |
|---|---|---|
| E20 | 缺少 `keyword` | HTTP 422，错误定位 `keyword` |
| E21 | `keyword=""` | HTTP 422，违反最小长度 |
| E22 | `keyword` 为整数或对象 | HTTP 422，类型错误 |
| E23 | `gap_days=0` | HTTP 422 |
| E24 | `gap_days=91` | HTTP 422 |
| E25 | `gap_days` 为非整数字符串 | HTTP 422 |
| E26 | `limit=0` | HTTP 422 |
| E27 | `limit=201` | HTTP 422 |
| E28 | `limit` 为非整数字符串 | HTTP 422 |
| E29 | `window_days=0` | HTTP 422 |
| E30 | `window_days` 为非整数字符串 | HTTP 422 |
| E31 | `exclude_promo` 为非布尔值 | HTTP 422 |

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

- 原有 76 条旧接口用例，更新受影响的字段、排序和 DB 规则。
- `E01-E31` 事件召回用例，其中 E01-E19 为正向/数据一致性场景，E20-E31 为参数负向场景。
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
