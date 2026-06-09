# 因子库接口用例与自动化第一版设计

## 1. 目标

第一版同时完成两件事：

1. 生成可复制到 XMind 的接口用例结构文档。
2. 实现传统 pytest 接口自动化的第一步：登录鉴权 + `GET /api/v1/factors` 因子列表接口。

第一版不是全量接口实现，而是先验证两个能力：

- 场景设计能力：能看到因子库核心业务链路，以及后续扩展位置。
- 单接口自动化能力：能把一个核心只读接口做完整，包括接口断言、DB 对账、上下游一致性和 Allure 输出。

## 2. 范围与边界

### 2.1 本次范围

- 测试环境 API：`https://test-factor-backend.questvector.ai`
- 登录账号：`haoran@gmail.com`
- 数据库：`factor_db`
- 自动化范围：
  - `POST /api/v1/auth/login`
  - `GET /api/v1/factors`
  - 主题、子因子、IC/IR 相关只读接口作为上下游一致性辅助接口
- DB 校验范围：
  - `factors`
  - `factors_details`
  - `factors_status`
  - `factor_theme_relations`
  - `themes`
  - 若因子列表接口返回 IC/IR 字段，则对账 `factor_ic_summary_metrics`

### 2.2 不在本次范围

- 不跑正式环境。
- 不做全量接口自动化。
- 不做写接口自动化，例如创建、更新、审批处理、删除。
- 不做 YAML 驱动或 YAML 自动生成代码。
- 不生成独立 DB 报告，不做数据修复。
- 暂不纳入“补漏专项”的全量边界场景，例如登录失败次数锁定、字段长度全边界、全量状态流转；这些放到后续全量实现前再集中确认。

### 2.3 YAML 的角色

`因子库.yaml` 只作为接口文档参考，用于前期理解接口路径、方法、参数、响应含义和模块划分。

用例设计完成后，YAML 不参与自动化运行，不作为运行时校验标准，也不用于生成代码。

自动化实现采用传统方式：

- `api/platform/` 手写接口封装。
- `tests/factor_library/api/` 手写 pytest 用例。
- `infrastructure/` 手写 DB 与断言支撑能力。

## 3. XMind 用例设计

### 3.1 输出格式

XMind 文档使用缩进文本，便于直接复制：

```text
模块
  功能分类
    用例名称
      操作
        结果
```

规则：

- 结果必须作为操作的子节点，不和操作同级。
- 保留简短编号，但不写优先级、“步骤”、“预期”等字样。
- 用例标题说明测试目的，不靠编号理解用例。
- 操作节点写清楚接口名称、operationId 和接口路径。
- 以业务场景组织，不按 YAML 文件顺序机械翻译接口。

### 3.2 第一层模块

```text
因子库接口用例
  登录鉴权
  因子列表
  主题结构
  子因子
  IC/IR评价
  审批流转
  权限异常
  数据一致性
```

编号规则：

```text
AU-xx  登录鉴权
FA-xx  因子列表
TH-xx  主题结构
SF-xx  子因子
IC-xx  IC/IR评价
AP-xx  审批流转
PE-xx  权限异常
DC-xx  数据一致性
```

### 3.3 颗粒度

- `登录鉴权` 和 `因子列表` 写到可执行步骤级。
- `主题结构`、`子因子`、`IC/IR评价`、`审批流转`、`权限异常`、`数据一致性` 第一版写到场景和关键检查点级。
- 后续新增接口时只新增编号，不改旧编号。
- 同一个接口可以出现在多个业务模块，但同一检查点只归属一个主用例；其它模块只引用，不重复写完整断言。

### 3.4 XMind 样稿

```text
因子库接口用例
  登录鉴权
    登录
      AU-01 有效账号登录成功
        使用 haoran@gmail.com 请求 login，POST /api/v1/auth/login
          返回 success=true，data.token 不为空，data.user.email 与登录账号一致
      AU-02 错误密码登录失败
        使用正确邮箱和错误密码请求 login，POST /api/v1/auth/login
          返回明确的鉴权失败信息，不返回 token
    鉴权拦截
      AU-03 未带 token 查询因子列表
        不带 Authorization 请求 listFactors，GET /api/v1/factors
          返回未授权错误，不返回因子列表数据

  因子列表
    查询
      FA-01 查询因子列表成功
        使用有效 token 请求 listFactors，GET /api/v1/factors?page=1&limit=20
          返回 success=true，items 为因子列表，pagination 信息完整
          每条因子包含基础信息、factor_detail、themes
          接口返回数据与 DB 查询结果一致
    分页
      FA-02 查询第一页因子列表
        请求 listFactors，GET /api/v1/factors?page=1&limit=5
          返回 5 条以内数据，pagination.page=1，pagination.limit=5，pagination.total 与 DB 总数一致
      FA-03 切换分页大小
        请求 listFactors，GET /api/v1/factors?page=1&limit=50
          返回 50 条以内数据，当前页数据顺序与 DB 分页结果一致
    排序
      FA-04 按更新时间升序查询
        请求 listFactors，GET /api/v1/factors?sort_by=updated_at&sort_order=asc
          返回数据按更新时间升序排列，顺序与 DB 查询结果一致
    筛选
      FA-05 按主题筛选因子
        请求 listFactors，GET /api/v1/factors?factor_theme=sentiment
          返回因子均归属于 sentiment 主题，接口 themes 与 DB 主题关系一致
    数据对账
      FA-DB-01 校验因子基础字段
        请求 listFactors 后按返回 id 查询 DB factors 表
          id、serial_number、factor_name、cn_name、level、max_level、child_factor_count、created_at、updated_at 与 DB 一致
      FA-DB-02 校验因子详情字段
        请求 listFactors 后按 factor_id 查询 DB factors_details 表
          factor_detail 中 name、status、strategy_status、update_interval、hit_count 与 DB 一致
      FA-DB-03 校验主题归属
        请求 listFactors 后按 factor_id 查询 DB factor_theme_relations 和 themes
          接口返回 themes 数量、theme_key、theme_name、cn_name、status 与 DB 一致
```

完整 XMind 文档在实现前单独输出。

## 4. 自动化架构

### 4.1 目录设计

```text
api/platform/
  auth_api.py
  factor_library_api.py

infrastructure/db/
  mysql_client.py
  ssh_tunnel.py

infrastructure/assertions/
  factor_library_asserts.py

tests/factor_library/api/
  test_factor_list_api.py

data/
  factor_library_api_cases.yaml
```

本次需求明确允许新增或修改 `infrastructure/`。DB 对账属于接口自动化支撑能力，但必须保持只读。

### 4.2 模块职责

`api/platform/auth_api.py`

- 封装 `/api/v1/auth/login`。
- 使用邮箱和密码登录。
- 返回 JWT token 和用户信息。
- 不做业务断言，只负责请求。

`api/platform/factor_library_api.py`

- 封装因子库相关只读接口。
- 第一版至少包含：
  - `GET /api/v1/factors`
  - `GET /api/v1/themes`
  - `GET /api/v1/factors/theme-tree`
  - `GET /api/v1/sub-factors`
  - `GET /api/v1/factor-ic/factors/{factor_id}/summary`
- 第一版主测 `GET /api/v1/factors`。
- 其它接口用于上下游一致性辅助验证。

`infrastructure/db/mysql_client.py`

- 读取 `config/env.test` 中的 DB 配置。
- 支持直接连接 MySQL。
- 只允许执行 `SELECT` 类只读查询。
- 查询结果统一转为 `list[dict]`。

`infrastructure/db/ssh_tunnel.py`

- 当配置启用 SSH 时，先建立 SSH tunnel，再连接 MySQL。
- 未启用 SSH 时直接连接 MySQL。

`infrastructure/assertions/factor_library_asserts.py`

- 断言接口响应结构。
- 断言分页、排序、筛选。
- 断言接口数据与 DB 一致。
- 断言因子列表和主题、子因子、IC/IR 接口数据一致。

`tests/factor_library/api/test_factor_list_api.py`

- 组织登录、请求接口、查询 DB、调用断言。
- 负责 Allure metadata。
- 不直接处理 SSH tunnel 或 SQL 细节。

## 5. 配置设计

`config/env.test` 增加或使用以下配置：

```text
BASE_URL=https://test-factor-backend.questvector.ai
FACTOR_EMAIL=haoran@gmail.com
FACTOR_PASSWORD=Aa%@#haoran

FACTOR_DB_HOST=43.167.190.122
FACTOR_DB_PORT=3306
FACTOR_DB_NAME=factor_db
FACTOR_DB_USER=factor_app
FACTOR_DB_PASSWORD=-RL1Zivb6wIzf4CmqJp6KQ6p

FACTOR_SSH_ENABLED=true
FACTOR_SSH_HOST=43.167.190.122
FACTOR_SSH_PORT=22
FACTOR_SSH_USER=appview
FACTOR_SSH_KEY_PATH=/Users/wrh/.ssh/id_rsa
FACTOR_SSH_PASSWORD=woxiangni.
```

后续如增加多账号，可扩展为：

```text
FACTOR_NORMAL_EMAIL=
FACTOR_NORMAL_PASSWORD=
FACTOR_APPROVER_EMAIL=
FACTOR_APPROVER_PASSWORD=
FACTOR_ADMIN_EMAIL=
FACTOR_ADMIN_PASSWORD=
```

第一版先使用已提供账号。

## 6. 数据流

```text
读取 config/env.test
  登录接口获取 JWT
    请求 GET /api/v1/factors
      查询 DB 对应数据
        必要时请求 themes / theme-tree / sub-factors / factor-ic summary
          执行断言
            输出 Allure 结果和差异详情
```

## 7. DB 对账口径

`GET /api/v1/factors` 的对账流程：

```text
接口参数 page / limit / filter / sort
  映射为只读 SQL 查询条件
    查询 factors 主表
    LEFT JOIN factors_details
    LEFT JOIN factor_theme_relations
    LEFT JOIN themes
    按接口同样规则分页排序
      对比 pagination.total
      对比 items 数量和顺序
      对比每个 item 的基础字段
      对比 factor_detail
      对比 themes
```

严格规则：

- 接口实际返回中属于因子列表输出的字段必须和 DB 一致。
- 接口返回了 DB 可追溯字段，就必须能在对应表查到并一致。
- DB 中按相同查询条件应返回的数据，接口分页、筛选、排序后的结果必须一致。
- 字段命名可以不同，但必须有清晰映射。
- 如果接口响应没有包含某个 DB 字段，不把它纳入该接口断言范围。

IC/IR 对账规则：

- 如果因子列表接口返回 IC/IR 字段，则必须能映射到 `factor_ic_summary_metrics` 并一致。
- 如果因子列表接口不返回 IC/IR 字段，第一版不强行查 IC 表对账；IC/IR 由后续专项接口覆盖。

上下游一致性：

- `listFactors` 返回的 `themes` 必须能在 `listThemes` 或 `theme-tree` 中找到。
- `child_factor_count` 必须和子因子关系或子因子接口查询结果一致。
- 若后续列表返回评价字段，则必须和 `getFactorICSummary` 结果一致。

## 8. 错误处理

以下情况直接 fail：

- HTTP 状态码不符合接口行为。
- 登录成功但 token 缺失。
- `success` 字段和业务结果不一致。
- 分页总数、当前页数量、顺序和 DB 不一致。
- 接口字段值与 DB 字段值不一致。
- 接口返回的 theme 在 DB 或主题接口中找不到。
- 接口返回的 `factor_detail` 与 DB `factors_details` 不一致。
- 参数错误没有返回明确错误，而是 500 或静默返回错误数据。

以下情况 skip：

- `BASE_URL` 未配置。
- 登录账号未配置。
- DB 校验用例启用但 DB 连接配置缺失。
- 当前环境网络不可达。

Allure 附件保留：

- 请求 URL 和 query 参数。
- 响应 JSON 摘要。
- DB SQL 名称和参数。
- 差异字段列表，例如 `factor_id=615, field=child_factor_count, api=87, db=84`。
- 上下游接口差异，例如 `theme_id=12 在 listFactors 返回，但 theme-tree 未出现`。

## 9. 第一版自动化用例类型

```text
登录鉴权
  正常登录
  错误密码
  未带 token 查询因子列表
  无效 token 查询因子列表

因子列表
  默认查询
  page / limit 分页
  sort_order 升降序
  status 筛选
  factor_theme 筛选
  time_window 筛选
  created_by / operator_by 筛选
  日期范围筛选
  参数异常

DB 对账
  分页总数一致
  当前页 items 顺序一致
  因子基础字段一致
  factor_detail 一致
  themes 归属一致
  如接口返回 IC/IR 字段，则与 factor_ic_summary_metrics 一致

上下游一致性
  因子列表 themes 与主题列表一致
  因子列表 child_factor_count 与子因子查询结果一致
  因子列表 IC/IR 字段与 IC summary 接口一致
```

## 10. 验收标准

XMind 结构：

- 可直接复制进 XMind。
- 核心链路和 `GET /api/v1/factors` 到可执行颗粒度。
- 其它模块保留扩展入口，后续补接口时只新增，不推翻旧结构。
- 符合 `模块 → 功能分类 → 用例名称 → 操作 → 结果`。

自动化代码：

- 能通过邮箱密码登录获取 JWT。
- 能请求 `GET /api/v1/factors`。
- 覆盖分页、排序、筛选、鉴权异常。
- 能连接测试环境 DB 并执行只读对账。
- 接口与 DB 不一致时直接失败，并指出对象和字段。
- 能做至少一类上下游接口一致性校验。
- pytest 和 Allure 输出能定位失败原因。

执行命令：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/factor_library/api -v --env=test
```

## 11. 后续全量实现前再确认

后续全量实现前，需要单独确认补漏专项：

- 登录失败次数限制和账号锁定规则。
- token 过期策略。
- 各写接口字段长度上限和最小值。
- 创建、更新、审批、删除的状态流转规则。
- 多账号权限边界。
- 全量接口的 DB 映射关系。
- 全量接口的上下游一致性矩阵。
