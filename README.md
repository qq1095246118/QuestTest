# 量化数据中台 API 自动化测试框架 (Quant API AutoTest Framework)

## 📖 框架简介

本框架是专为**量化数据中台**（多交易所数据获取与整合 API）设计的基于 `pytest` 的自动化测试解决方案。
与常规的业务 API 测试框架不同，量化因子系统对数据质量和数据一致性极其敏感，因此本框架的设计核心聚焦于以下四大基石：

1. **数据一致性防御 (Defensive Testing)**：严格区分接口连通性与金融数据逻辑（如 OHLC 关系、时间戳规范），要求每一项业务测试都经过严苛的逻辑验证。
2. **底层数据校验 (DB Data Verification)**：不仅测试接口层面的返回，同时通过底层的 `DBClient` 直接与 MySQL 数据中台对接，确保接口吐出数据与底层存储数据的一致性。
3. **高可用与容错 (High Availability)**：内置基于指数退避算法的重试机制，从容应对各类交易所或网关频发的 429 限流策略及瞬时网络抖动。
4. **AI 辅助开发纪律 (AI Generation Guard)**：建立严格的目录纪律与断言规范，设立测试边界限制，确保 AI 生成代码不破坏基础设施。

---

## 📂 目录结构与分层模型 (Layered Architecture)

框架采用了高内聚、低耦合的清晰分层模型，具体目录及核心文件职责如下：

### 1. 配置层 (`config/`)
负责管理多环境切换与全局变量配置。
- **`settings.py`**: 基于 `Pydantic BaseSettings` 编写的配置管理器。通过读取操作系统的环境变量动态加载对应的 `.env` 文件，实现开发、测试、生产环境的平滑切换。包括接口地址、可选 API Key、数据库连接（`db_host`, `db_port`, `db_user` 等）信息的加载。
- **`.env.example`**: 环境变量模板。实际 `.env.test` / `.env.prod` 为本地私有配置，不提交到代码库。

### 2. 核心基建层 (`core/`)
**【框架禁区】禁止普通业务测试开发与 AI 助手直接修改。** 存放框架的底层驱动逻辑。
- **`http_client.py`**: 统一的网络请求客户端。封装了 `requests`，并深度集成了 `Tenacity` 库，实现了自动重试机制（专治 HTTP 429 和 502/504 等状态码）。
- **`db_client.py`**: 底层 MySQL 数据库连接客户端。基于 `PyMySQL` 的 `DictCursor` 封装，提供直连数据库查询方法（`query`），为数据质量控制（DQC）提供底层数据比对依据。
- **`dqc_asserts.py`**: 数据质量控制（DQC）断言库。提供 JSON Schema 结构验证、数字精度防溢出验证以及 13 位毫秒级时间戳规范校验等。
- **`logic_asserts.py`**: 金融逻辑断言库。提供强业务属性的校验，例如 K线 (OHLC) 关系校验（最高价必须大于等于其他价）、时间序列连续性校验（防止漏线跳线）。

### 3. 接口服务层 (`api_services/`)
面向对象的 API 路由封装层，将繁琐的 HTTP 细节下沉。
- **`base_api.py`**: 提供基础的 HTTP 请求方法（GET/POST 等），并统一处理 JSON 请求头；外部 Binance 行情接口可选使用 API Key 提升限流额度。Kline 数据中台接口只发送 JSON 请求头。

### 4. 数据驱动层 (`data/`)
实现测试脚本与测试数据的彻底解耦。
- **`test_symbols.yaml`**: 存放测试用例所需的参数化数据（如测试币对 `BTC-USDT`、交易所枚举等）。配合 `pytest` 参数化机制，实现一套用例跑多套数据。

### 5. 测试用例层 (`tests/`)
实际的自动化测试用例落盘处。
- **`conftest.py`**: Pytest 的全局 Hook 与 Fixture 配置文件。实现了基于命令行参数 `--env` 动态切换环境，并预埋了测试执行完毕后向企微/钉钉发送汇总告警信息的逻辑。
- **`test_kline_api.py`**: Kline Data 七个 legacy 接口的传统 pytest 用例，每个接口保留 Normal、ParamError、Boundary、Response、Performance 五类用例。

### 6. 文档与规范 (`docs/`)
- **`AI_GENERATION_GUIDE.md`**: AI 代码生成指南。设立了 4 条不可逾越的规则（禁止修改 Core 目录、强制使用专属断言库、目录结构规范、**严格的测试范围限制**），防止大模型在后续扩展中污染框架结构。

### 7. 根目录基础设施
- **`pytest.ini`**: Pytest 核心配置文件。定义了常用的标签（markers: `smoke`, `dqc`, `logic`）、默认运行参数以及 Python 的包检索路径 (`pythonpath = .`)。
- **`requirements.txt`**: Python 第三方依赖清单（包含 `pytest`, `requests`, `tenacity`, `pydantic>=2.7.0`, `pydantic-settings`, `python-dotenv`, `pymysql` 等核心依赖）。
- **`Jenkinsfile`**: 声明式的 CI/CD 流水线配置文件。包含拉取代码、构建虚拟环境、执行测试、生成 Allure 报告及发送 IM 告警的完整 Pipeline 步骤。

---

## 🚀 核心能力与开发指南

### 1. 编写一个标准的 API 测试
所有新增的业务接口测试必须遵循以下流程：
1. 在 `api_services/` 下新建对应模块继承 `BaseAPI`。
2. 在 `data/` 下准备相关的 YAML 驱动数据。
3. 在 `tests/` 目录下编写 `test_xxx.py`，必须且只能使用 `core.logic_asserts` 与 `core.dqc_asserts` 验证金融数据，禁止直接使用普通的 `assert True` 或单纯检查 HTTP 状态码（状态码和重试已由 `http_client` 兜底）。

### 2. 数据库一致性校验
当需要验证 API 下发的数据与底层数据库一致时：
```python
from core.db_client import DBClient

def test_data_consistency():
    db = DBClient()
    # 框架默认已通过 settings 获取数据库连接信息
    result = db.query("SELECT * FROM binance_1h_usdm_kline_raw LIMIT 1")
    # ...与 API 结果比对...
    db.close()
```

### 3. Binance 数据库准确性全量校验
`tests/test_binance_db_accuracy.py` 提供手动触发的 Binance raw/metadata 表全量准确性校验。它会从 MySQL 扫描 PDF 范围内的 Binance raw/metadata 表，并与 Binance REST 上游严格对账。

默认 `pytest` 不运行该套件；需要显式传入：

```bash
python3 -m pytest tests/test_binance_db_accuracy.py -v --run-db-accuracy
```

更多运行方式见 `docs/binance_db_accuracy_validation.md`。

### 4. 测试范围控制 (Scope Restriction)
当前框架的测试边界被**严格限制于数据中台 PDF 文档内记载的核心表与接口**（如 `binance_1h_usdm_kline_raw` 等）。禁止擅自编写针对链上数据、新闻资讯、Meme 监控等未定型的接口测试，以免产生大量无效用例。

---

## 💻 快速开始

### 1. 环境准备
```bash
# 使用本机 pyenv Python 3.12
PYTHON=/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12

# 安装依赖
$PYTHON -m pip install -r requirements.txt
```

### 2. 环境变量配置
基于模板创建本地环境配置，补充真实接口地址和数据库信息：
```bash
cp config/.env.example config/.env.test
```

### 3. 运行测试
```bash
# 运行全部测试，确保使用本机 Python 3.12
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v

# 运行特定环境的测试 (由 conftest.py 拦截并设置环境变量)
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --env=prod

# 仅运行包含特定标记的测试 (例如仅验证数据逻辑)
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v -m logic

# 运行测试并收集 Allure 报告数据
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest -v --alluredir=./allure-results
```

### 4. 生成 Allure 报告
```bash
# 生成静态报告（需要本机已安装 Allure CLI）
allure generate ./allure-results -o ./allure-report --clean

# 打开静态报告
allure open ./allure-report

# 或直接启动临时报告服务
allure serve ./allure-results
```

---

## 🛡️ 架构师要求与规范提示 (For QA/Tester)

作为量化体系下的 QA/Tester，请时刻铭记：
1. **数据准确性高于一切**：K线的跳空、精度截断、甚至是时间戳的毫秒/秒级错乱，都可能导致下游策略跑出巨大回撤。测试的重点永远在 `dqc_asserts` 与 `logic_asserts`。
2. **防御性编程**：不要信任任何外部接口。重试机制、超时配置、脏数据过滤必须在框架的每一层落实。
3. **敬畏纪律**：AI 助手和开发人员在贡献代码时，必须严格遵守 `docs/AI_GENERATION_GUIDE.md` 约定的纪律，保护核心基建层 (`core/`) 的纯洁性。
