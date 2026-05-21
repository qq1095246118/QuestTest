# Funding Window and Report Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 direct 模式 funding 表窗口过大导致 Binance source 被 `limit=1000` 截断的误报，并修复 xlsx 已知异常类型中文说明。

**Architecture:** 复用现有 `TableSpec.fixed_interval` 表达 funding 源端 cadence，不新增模型层级。`DBAccuracyReader.build_windows()` 对 funding 表按 `fixed_interval` 或默认 `8h` 计算安全窗口；报告脚本只补齐已存在 reason-note 映射的返回逻辑。

**Tech Stack:** Python 3.12、pytest、YAML 表配置、现有 DB accuracy direct runner、现有 xlsx 生成脚本。

---

## 文件结构

- 修改 `data/binance_db_accuracy_tables.yaml`
  - 给 `binance_1h_usdm_funding_rate_raw` 增加 `fixed_interval: 1h`。
- 修改 `tests/db_accuracy/db_reader.py`
  - 增加 funding 默认 interval 常量。
  - 将 funding 的窗口从固定 90 天改为基于 interval + request_limit 的安全窗口。
- 修改 `tests/test_db_accuracy_config.py`
  - 增加 `binance_1h_usdm_funding_rate_raw` 配置断言。
  - 增加 explicit `1h` funding 窗口切分测试。
  - 替换旧的 90 天 funding 窗口预期为默认 `8h` cadence。
- 修改 `scripts/build_db_accuracy_allure_xlsx.py`
  - `_describe_difference()` 命中已知 `notes` 时直接返回对应中文说明。
- 修改 `tests/test_build_db_accuracy_allure_xlsx.py`
  - 增加已知 `missing_source_row` 说明文案测试。

执行前先运行 `git status --short`。如果这些文件已有非本计划改动，不要用整文件 `git add` 混入无关内容；先和用户确认或只报告改动，不要误提交。

---

### Task 1: 为 funding 窗口策略写失败测试

**Files:**
- Modify: `tests/test_db_accuracy_config.py`
- Test: `tests/test_db_accuracy_config.py`

- [ ] **Step 1: 在 `tests/test_db_accuracy_config.py` 增加 1h 表配置测试**

把下面测试放在 `test_loaded_specs_have_key_and_compare_fields()` 后面：

```python
def test_loaded_one_hour_usdm_funding_spec_declares_fixed_interval():
    specs = {spec.table: spec for spec in load_table_specs()}

    assert specs["binance_1h_usdm_funding_rate_raw"].fixed_interval == "1h"
```

- [ ] **Step 2: 替换旧的 90 天 funding 窗口测试**

找到现有 `test_reader_slices_funding_windows_at_ninety_days()`，用下面测试替换它：

```python
def test_reader_slices_funding_windows_with_default_eight_hour_cadence():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_funding",
        kind="funding",
        endpoint="funding_rate",
        key_fields=("symbol",),
        time_fields=("fundingTime",),
        interval_field=None,
        compare_fields=("fundingTime", "fundingRate"),
        request_limit=3,
    )
    resolved = resolve_spec(spec, {"symbol", "fundingTime", "fundingRate"})
    time_range = KeyTimeRange(
        table="sample_funding",
        key=ValidationKey({"symbol": "BTCUSDT"}),
        start_ms=0,
        end_ms=24 * hour_ms,
    )

    windows = DBAccuracyReader(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 24 * hour_ms - 1),
        (24 * hour_ms, 24 * hour_ms),
    ]
```

- [ ] **Step 3: 增加 explicit 1h funding 窗口测试**

把下面测试放在默认 `8h` funding 测试后面：

```python
def test_reader_slices_funding_windows_from_explicit_fixed_interval():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_one_hour_funding",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("funding_time", "funding_rate", "mark_price"),
        request_limit=3,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "funding_time", "funding_rate", "mark_price"})
    time_range = KeyTimeRange(
        table="sample_one_hour_funding",
        key=ValidationKey({"symbol": "0GUSDT"}),
        start_ms=0,
        end_ms=5 * hour_ms,
    )

    windows = DBAccuracyReader(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, 3 * hour_ms - 1),
        (3 * hour_ms, 5 * hour_ms),
    ]
```

- [ ] **Step 4: 增加 request_limit=1 的 funding 窗口测试**

把下面测试放在 explicit `1h` funding 测试后面：

```python
def test_reader_slices_funding_windows_with_request_limit_one():
    hour_ms = 3_600_000
    spec = TableSpec(
        table="sample_one_hour_funding",
        kind="funding",
        endpoint="usdm_funding",
        key_fields=("symbol",),
        time_fields=("funding_time",),
        interval_field=None,
        compare_fields=("funding_time", "funding_rate", "mark_price"),
        request_limit=1,
        fixed_interval="1h",
    )
    resolved = resolve_spec(spec, {"symbol", "funding_time", "funding_rate", "mark_price"})
    time_range = KeyTimeRange(
        table="sample_one_hour_funding",
        key=ValidationKey({"symbol": "0GUSDT"}),
        start_ms=0,
        end_ms=2 * hour_ms,
    )

    windows = DBAccuracyReader(FakeDB()).build_windows(resolved, time_range)

    assert [(window.start_ms, window.end_ms) for window in windows] == [
        (0, hour_ms - 1),
        (hour_ms, 2 * hour_ms - 1),
        (2 * hour_ms, 2 * hour_ms),
    ]
```

- [ ] **Step 5: 运行失败测试**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/test_db_accuracy_config.py::test_loaded_one_hour_usdm_funding_spec_declares_fixed_interval \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_with_default_eight_hour_cadence \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_from_explicit_fixed_interval \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_with_request_limit_one \
  -q
```

Expected: FAIL。至少应看到 `fixed_interval == "1h"` 失败，窗口断言也应体现当前实现仍是 90 天/整段窗口。

---

### Task 2: 实现 funding 安全窗口切分

**Files:**
- Modify: `data/binance_db_accuracy_tables.yaml`
- Modify: `tests/db_accuracy/db_reader.py`
- Test: `tests/test_db_accuracy_config.py`

- [ ] **Step 1: 配置 1h funding 表 interval**

在 `data/binance_db_accuracy_tables.yaml` 的 `binance_1h_usdm_funding_rate_raw` 配置中加入 `fixed_interval: 1h`：

```yaml
  - table: binance_1h_usdm_funding_rate_raw
    kind: funding
    endpoint: usdm_funding
    key_fields: [symbol]
    time_fields: [funding_time, timestamp]
    interval_field:
    fixed_interval: 1h
    compare_fields: [symbol, funding_rate, funding_time, mark_price]
    request_limit: 1000
```

- [ ] **Step 2: 在 `tests/db_accuracy/db_reader.py` 增加 funding 默认 interval**

在 import 和 `_IDENTIFIER_RE` 之间加入常量：

```python
DEFAULT_FUNDING_INTERVAL = "8h"
```

- [ ] **Step 3: 修改 `build_windows()` 的 funding 分支**

将 `build_windows()` 开头替换成下面结构。注意：因为窗口 `end_ms` 是闭区间，funding 安全窗口使用 `request_limit * interval_ms - 1` 作为窗口跨度，这样 exact cadence 下最多包含 `request_limit` 个时间点，同时 `request_limit=1` 不会退化成 1ms 小窗口。

```python
    def build_windows(
        self,
        spec: ResolvedTableSpec,
        time_range: KeyTimeRange,
    ) -> list[ValidationWindow]:
        if spec.spec.request_limit < 1:
            raise ValueError("request_limit must be >= 1")

        if spec.spec.kind == "funding":
            interval = spec.spec.fixed_interval or DEFAULT_FUNDING_INTERVAL
            window_end = _funding_window_end(interval, spec.spec.request_limit)
        else:
            interval = spec.spec.fixed_interval
            if interval is None:
                if spec.interval_field is None:
                    raise ValueError(
                        f"{spec.spec.table} requires fixed_interval or interval_field to build windows"
                    )
                interval = str(time_range.key.values[spec.interval_field])
            interval_count = spec.spec.request_limit
            window_end = _kline_window_end(spec.spec.endpoint, interval, interval_count)
```

- [ ] **Step 4: 增加 `_funding_window_end()` helper**

放在 `_fixed_window_end()` 前面：

```python
def _funding_window_end(interval: str, request_limit: int):
    window_span_ms = interval_to_ms(interval) * request_limit

    def window_end(start_ms: int) -> int:
        return start_ms + window_span_ms - 1

    return window_end
```

- [ ] **Step 5: 运行 funding 窗口测试**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/test_db_accuracy_config.py::test_loaded_one_hour_usdm_funding_spec_declares_fixed_interval \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_with_default_eight_hour_cadence \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_from_explicit_fixed_interval \
  tests/test_db_accuracy_config.py::test_reader_slices_funding_windows_with_request_limit_one \
  -q
```

Expected: PASS。

- [ ] **Step 6: 提交 funding 窗口修复**

如果 `git status --short` 显示这些文件没有预先存在的无关改动，提交：

```bash
git add data/binance_db_accuracy_tables.yaml tests/db_accuracy/db_reader.py tests/test_db_accuracy_config.py
git commit -m "fix: bound funding source windows"
```

如果这些文件已有用户改动，不要提交；记录 `git diff -- data/binance_db_accuracy_tables.yaml tests/db_accuracy/db_reader.py tests/test_db_accuracy_config.py` 的关键变化并交给用户确认。

---

### Task 3: 为 xlsx 已知异常说明写失败测试

**Files:**
- Modify: `tests/test_build_db_accuracy_allure_xlsx.py`
- Test: `tests/test_build_db_accuracy_allure_xlsx.py`

- [ ] **Step 1: 增加 known reason 文案测试**

在 `test_direct_payload_xlsx_has_chinese_headers_and_text_values()` 后面加入：

```python
def test_describes_known_missing_source_row_reason():
    module = _load_script_module()

    assert module._describe_difference("missing_source_row", "funding_time") == (
        "DB 中存在该 key，但源接口未返回对应行，需确认第三方接口口径或 DB 是否保留了旧数据。"
    )
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/test_build_db_accuracy_allure_xlsx.py::test_describes_known_missing_source_row_reason \
  -q
```

Expected: FAIL。当前实现会返回 `未归类异常；字段 funding_time 的异常类型为 missing_source_row。`

---

### Task 4: 实现 xlsx 已知 reason 文案映射

**Files:**
- Modify: `scripts/build_db_accuracy_allure_xlsx.py`
- Test: `tests/test_build_db_accuracy_allure_xlsx.py`

- [ ] **Step 1: 修改 `_describe_difference()`**

在 `notes = {...}` 字典结束后、`reason.startswith("window_error:")` 之前加入：

```python
    if reason in notes:
        return notes[reason]
```

修改后的结构应是：

```python
def _describe_difference(reason: str, field: str) -> str:
    notes = {
        "value_mismatch": "同一 key 的字段值不一致，优先检查采集转换、精度归一化和数据源返回值。",
        "missing_db_row": "源接口存在该 key，但 DB 中没有对应行，说明可能漏写入或过滤条件不一致。",
        "missing_source_row": "DB 中存在该 key，但源接口未返回对应行，需确认第三方接口口径或 DB 是否保留了旧数据。",
        "missing_db_field": "源数据有该字段，但 DB 行缺少该字段。",
        "missing_source_field": "DB 行有该字段，但源数据缺少该字段。",
        "missing_both_fields": "DB 和源数据都缺少该字段，需检查表配置中的 compare_fields。",
        "missing_db_row_key_field": "DB 行缺少用于关联的 key 字段。",
        "null_db_row_key": "DB 行用于关联的 key 为空。",
        "null_source_row_key": "源数据用于关联的 key 为空。",
        "duplicate_db_row_key": "DB 中同一 key 出现重复行。",
        "duplicate_source_row_key": "源数据中同一 key 出现重复行。",
        "no_stable_db_rows": "稳定窗口内未找到 DB 数据，无法和源数据对比。",
        "no_windows_checked": "没有生成可检查的时间窗口。",
    }
    if reason in notes:
        return notes[reason]
    if reason.startswith("window_error:"):
        return "该时间窗口请求或对比失败，请结合异常类型中的错误信息定位接口、网络或数据格式问题。"
```

- [ ] **Step 2: 运行 xlsx 文案测试**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/test_build_db_accuracy_allure_xlsx.py::test_describes_known_missing_source_row_reason \
  -q
```

Expected: PASS。

- [ ] **Step 3: 提交 xlsx 文案修复**

如果 `git status --short` 显示这些文件没有预先存在的无关改动，提交：

```bash
git add scripts/build_db_accuracy_allure_xlsx.py tests/test_build_db_accuracy_allure_xlsx.py
git commit -m "fix: describe known db accuracy differences"
```

如果这些文件已有用户改动，不要提交；记录 `git diff -- scripts/build_db_accuracy_allure_xlsx.py tests/test_build_db_accuracy_allure_xlsx.py` 的关键变化并交给用户确认。

---

### Task 5: 相关回归验证

**Files:**
- Test: `tests/test_db_accuracy_config.py`
- Test: `tests/test_build_db_accuracy_allure_xlsx.py`

- [ ] **Step 1: 运行两个相关测试文件**

Run:

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest \
  tests/test_db_accuracy_config.py \
  tests/test_build_db_accuracy_allure_xlsx.py \
  -q
```

Expected: PASS。

- [ ] **Step 2: 做安全的收尾检查**

Run:

```bash
git status --short
git diff -- data/binance_db_accuracy_tables.yaml tests/db_accuracy/db_reader.py tests/test_db_accuracy_config.py scripts/build_db_accuracy_allure_xlsx.py tests/test_build_db_accuracy_allure_xlsx.py
```

Expected:

- 只看到本计划范围内的改动。
- 没有 `core/` 改动。
- 没有 cached runner、DataComPy、DB 数据写入相关改动。

- [ ] **Step 3: 如需人工复核 direct mode，运行小范围 live 命令**

仅在用户允许访问远程 DB 和 Binance 时运行：

```bash
/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12 -m pytest tests/test_binance_db_accuracy.py -v \
  --env=test \
  --run-db-accuracy \
  --db-accuracy-mode direct \
  --db-accuracy-table binance_1h_usdm_funding_rate_raw \
  --db-accuracy-safety-hours 24
```

Expected:

- 不应再出现由 90 天窗口 `limit=1000` 截断造成的批量 `missing_source_row`。
- 如果仍有 `missing_source_row`，需要检查其是否为真实 DB-only 行，而不是窗口中第 1001 条之后连续缺失。

---

## 自检清单

- Spec 覆盖：
  - direct-mode funding 窗口过大：Task 1、Task 2、Task 5 覆盖。
  - `binance_1h_usdm_funding_rate_raw` 显式 1h 支持：Task 1 Step 1、Task 2 Step 1 覆盖。
  - 真实 `missing_source_row` 不被忽略：本计划只改窗口，不改 compare 逻辑。
  - xlsx 已知异常文案：Task 3、Task 4 覆盖。
- 留空项检查：本计划没有留空项或待补细节。
- 类型一致性：
  - 使用现有 `TableSpec.fixed_interval`，不新增 dataclass 字段。
  - `DBAccuracyReader.build_windows()` 仍返回 `list[ValidationWindow]`。
  - 新 helper `_funding_window_end(interval: str, request_limit: int)` 只在 `db_reader.py` 内部使用。
