# 量化数据中台 API 自动化测试框架

QuestTest 是一个面向量化数据中台的 Python 3.12 + pytest 自动化测试框架。
它不只验证接口是否能访问，更重点保护金融数据质量：响应契约、毫秒级时间戳、
OHLC 逻辑、DB 与上游 Binance REST 数据的一致性。

## 核心目标

1. 数据准确性优先：K 线跳空、精度截断、时间戳错位都可能影响下游策略。
2. 分层清晰：原始 API 调用、业务判断、基础设施、测试用例、工具脚本各归其位。
3. 可追溯验证：普通 API 测试、DB consistency、Binance DB-to-source accuracy 都有固定入口。
4. AI 生成纪律：后续新增代码必须遵守目录职责、断言规范和测试边界。

## 当前目录结构

```text
QuestTest/
  api/                         # 原始 API 请求封装
    base_api.py
    platform/                  # 内部数据中台接口
    external/binance/          # Binance Spot / USDM / COIN-M 上游接口
  services/                    # 中间业务逻辑、判断、比较、缓存、报告数据准备
    db_accuracy/
      direct/                  # direct 模式 DB accuracy 编排
      cached/                  # cached 模式的 shard、frame、DataComPy 复用服务
      partitioned/             # 统一分区 runner、DB/source/compare 缓存和续跑编排
      reporting/               # 结果序列化
  infrastructure/              # 受保护基础设施层
    http/                      # HTTP retry client
    database/                  # DB client / DAO
    assertions/                # DQC 和金融逻辑断言
  tests/                       # 只放可执行 pytest 测试文件
    kline/api/
    binance/api/
    coinglass/api/
    factor_data/api/
    open_interest/api/
    db_accuracy/
      integration/
      services/
      tools/
  tools/                       # 可直接运行的工具脚本和临时 Python 文件
    db_accuracy/
  data/                        # YAML 测试数据和 DB accuracy 表规格
  config/                      # 环境配置
  docs/                        # 设计、规范和使用文档
  artifacts/reports/           # 当前手动报告输出目录
```

## 分层边界

### `infrastructure/`

基础设施层，默认不要改动，除非明确要求。

- `http/http_client.py`：统一 HTTP 客户端和重试策略。
- `database/db_client.py`：MySQL 连接和查询封装。
- `database/dao.py`：面向数据中台表的 DAO 查询。
- `assertions/dqc_asserts.py`：Schema、精度、毫秒级时间戳等 DQC 断言。
- `assertions/logic_asserts.py`：OHLC、时间序列连续性等金融逻辑断言。

### `api/`

只放原始接口调用封装，不放业务判断和对比逻辑。

- `api/platform/`：内部数据中台接口包装。
- `api/external/binance/`：Binance 上游行情接口包装。
- 新增 API 封装应继承或复用 `api/base_api.py`。

### `services/`

放中间逻辑、判断、比较、缓存和报告数据准备。

- DB accuracy 的统一执行入口在 `services/db_accuracy/partitioned/`，旧 direct/cached 服务作为底层能力复用。
- 可复用逻辑应优先放在这里，而不是测试文件或工具脚本里。

### `tests/`

只放 pytest 测试用例和 pytest 支撑文件。第一层按业务域或能力域组织，第二层再区分测什么。

```text
tests/<业务域>/<测试类型>/test_*.py
```

当前业务域：

- `kline/api/`：Kline Data legacy API 测试。
- `binance/api/`：Binance Full / Binance USDM 平台 API 测试。
- `coinglass/api/`：CoinGlass 相关 API 测试。
- `factor_data/api/`：Factor Data API 测试。
- `open_interest/api/`：Open Interest API 测试。
- `db_accuracy/integration/`：手动触发的 DB-to-source 集成校验入口。
- `db_accuracy/services/`：DB accuracy 服务逻辑单元测试。
- `db_accuracy/tools/`：根目录 `tools/db_accuracy/` 工具脚本的测试用例。

注意：`tests/db_accuracy/tools/` 是“工具测试”，不是工具实现。真正可运行的工具代码在根目录 `tools/`。

### `tools/`

放可直接运行的工具脚本和临时 Python 文件。

- `tools/db_accuracy/build_allure_xlsx.py`：把 DB accuracy Allure JSON 附件转换为中文 XLSX。
- `tools/db_accuracy/fetch_selected_usdm_klines.py`：拉取指定 USDM K 线样本并输出 CSV/JSON。
- `tools/db_accuracy/build_selected_usdm_klines_xlsx.py`：把专项 CSV/JSON 报告转换为中文 XLSX。

### `data/`

- `test_symbols.yaml`：通用参数化测试数据。
- `binance_db_accuracy_tables.yaml`：Binance DB accuracy 表规格，定义 table kind、endpoint、key fields、time fields、compare fields 等。

## 快速开始

推荐使用本机 Python 3.12：

```bash
PYTHON=/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12
```

安装依赖：

```bash
$PYTHON -m pip install -r requirements.txt
```

创建本地配置：

```bash
cp config/.env.example config/.env.test
```

真实 API 和 DB 测试需要补齐 `config/.env.<env>` 中的接口地址、数据库连接和可选 API Key。

## 常用命令

收集测试，适合结构调整后快速确认导入路径：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest --collect-only -q
```

运行全部测试：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v
```

按业务域运行：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/kline/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/binance/api -v
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/services -q
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/tools -q
```

切换环境：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --env=prod
```

按 marker 运行：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v -m logic
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v -m dqc
```

生成 Allure 原始结果：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --alluredir=./allure-results
```

生成或打开 Allure 报告：

```bash
allure generate ./allure-results -o ./allure-report --clean
allure open ./allure-report
```

## Binance DB Accuracy

DB accuracy 用来把 MySQL 中的 Binance raw/metadata 数据与 Binance REST 上游源数据做严格对账。
默认不参与普通 CI，只有显式传入 `--run-db-accuracy` 才会执行。

入口：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/integration/test_binance_db_accuracy.py -v --run-db-accuracy
```

当前 DB accuracy 入口统一使用 partitioned runner。Direct 和 cached 都会先规划 `table + market shard + time partition`，再准备 DB 分区缓存、准备 Binance source 分区缓存，最后统一 compare。

Direct 模式：

1. 从 `data/binance_db_accuracy_tables.yaml` 读取表规格。
2. 用 `services/db_accuracy/table_specs.py` 解析真实 DB 字段。
3. 用 `services/db_accuracy/db_reader_service.py` 发现稳定历史范围。
4. 用 `services/db_accuracy/partitioned/` 准备 DB/source 分区缓存。
5. 用 DataComPy 对比分区并写入 compare artifacts。
6. 用 `services/db_accuracy/reporting/result_serializer_service.py` 输出 Allure 附件 JSON/text。

Cached 模式适合大表和指定时间范围复核：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/db_accuracy/integration/test_binance_db_accuracy.py -v \
  --run-db-accuracy \
  --db-accuracy-mode cached \
  --db-accuracy-table binance_kline_all_future_raw \
  --db-accuracy-symbol BTCUSDT \
  --db-accuracy-interval 1m \
  --db-accuracy-start-ms 1704067200000 \
  --db-accuracy-end-ms 1704153599999 \
  --db-accuracy-use-db-cache true \
  --db-accuracy-use-source-cache true \
  --db-accuracy-workers 8
```

`--db-accuracy-use-db-cache true` 和 `--db-accuracy-use-source-cache true` 表示允许复用本地缓存；如果本次范围更大，缺失分区仍会重新获取。传 `false` 会覆盖对应数据侧的本地分区缓存。

完整参数、缓存结构、报告说明和排错方式见：

```text
docs/binance_db_accuracy_validation.md
```

## 工具和报告

当前手动报告输出目录是：

```text
artifacts/reports/
```

DB accuracy 默认缓存目录是：

```text
.cache/binance_accuracy/
```

该目录下会按 `db/`、`source/`、`compare/`、`runs/` 保存分区数据、对比产物和 run 汇总。

pytest/Allure 默认输出目录是：

```text
allure-results/
```

本地工作区可能还存在历史生成的 `reports/` 目录。它不是当前推荐输出目录，清理前需要确认是否仍有人工需要的历史产物。

## 新增或修改用例的规则

1. 先确认业务域。如果是 Kline，就放在 `tests/kline/`；如果是 DB accuracy，就放在 `tests/db_accuracy/`。
2. 再确认测试类型。API 用例放 `api/`，服务逻辑测试放 `services/`，集成入口放 `integration/`，工具测试放 `tools/`。
3. 原始接口调用封装放 `api/`。
4. 可复用判断、比较、清洗、缓存、报告准备逻辑放 `services/`。
5. 可直接运行的工具和临时 Python 文件放 `tools/`。
6. 测试数据和参数化数据放 `data/`。
7. 金融数据校验优先使用 `infrastructure/assertions/` 或服务层 validator，不要只检查 HTTP 状态码。

## 维护纪律

- 不要默认修改 `infrastructure/`。
- 不要把业务逻辑写进 `tests/` 或 `tools/`。
- 不要把可执行工具放进 `tests/`。
- 不要把测试用例直接堆在 `tests/` 根目录。
- 不要新增超出数据中台文档范围的外部表或接口测试。
- 保留用户已有改动，不要清理未明确要求处理的报告、缓存或计划文件。

## 参考文档

- AI 生成规则：`docs/AI_GENERATION_GUIDE.md`
- DB accuracy 使用说明：`docs/binance_db_accuracy_validation.md`
- Kline 传统用例说明：`docs/kline_traditional_test_report.md`
- DB accuracy 表规格：`data/binance_db_accuracy_tables.yaml`
- 后续 agent 快速入口：`AGENTS.md`
