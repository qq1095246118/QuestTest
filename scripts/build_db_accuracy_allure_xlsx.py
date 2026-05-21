from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_ALLURE_DIR = WORKSPACE / "allure-results"
DEFAULT_REPORTS_DIR = WORKSPACE / "reports"
DEFAULT_DB_ACCURACY_ALLURE_ROOT = DEFAULT_ALLURE_DIR / "db_accuracy"
EXCEL_MAX_ROWS = 1_048_576
DETAIL_HEADER = [
    "表名",
    "异常类型",
    "异常字段",
    "数据键",
    "定位键",
    "DB值",
    "源值",
    "DB时间(UTC)",
    "源时间(UTC)",
    "异常点说明",
]
DIRECT_TABLE_HEADER = ["表名", "是否通过", "窗口数", "DB行数", "源行数", "差异数"]
CACHED_SHARD_HEADER = [
    "分片",
    "分区",
    "状态",
    "DB行数",
    "源行数",
    "差异数",
    "报告文件",
    "差异文件",
    "异常信息",
]
INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TIMESTAMP_PATTERN = re.compile(
    r"(?:timestamp|open_time|close_time|funding_time|time|start_ms|end_ms)=([0-9]{10,16})",
    re.IGNORECASE,
)
INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")
TIMESTAMP_FIELDS = {
    "timestamp",
    "open_time",
    "close_time",
    "funding_time",
    "time",
    "start_time",
    "end_time",
    "start_ms",
    "end_ms",
}


@dataclass(frozen=True)
class SheetSpec:
    name: str
    rows: Sequence[Sequence[Any]]
    widths: Sequence[int]
    freeze_header: bool = True
    autofilter: bool = True


def load_json_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 顶层结构不是 JSON object")
    return payload


def is_accuracy_payload(payload: dict[str, Any]) -> bool:
    tables = payload.get("tables")
    shards = payload.get("shards")
    if isinstance(tables, list):
        return True
    return isinstance(shards, list)


def find_latest_accuracy_attachment(allure_dir: Path) -> Path:
    allure_dir = Path(allure_dir)
    if not allure_dir.exists():
        raise FileNotFoundError(f"Allure 目录不存在: {allure_dir}")

    candidates = {
        *allure_dir.rglob("*-attachment.json"),
        *allure_dir.rglob("*.json"),
    }

    for path in sorted(candidates, key=lambda item: (item.stat().st_mtime_ns, item.name), reverse=True):
        try:
            payload = load_json_payload(path)
        except ValueError:
            continue
        if is_accuracy_payload(payload):
            return path

    raise FileNotFoundError(f"未在 {allure_dir} 找到 db_accuracy_details JSON 附件")


def write_accuracy_workbook(payload: dict[str, Any], *, source_path: Path, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(payload.get("tables"), list):
        sheets = _build_direct_sheets(payload, source_path)
    elif isinstance(payload.get("shards"), list):
        sheets = _build_cached_sheets(payload, source_path)
    else:
        raise ValueError("JSON 中没有 tables 或 shards，无法转换为 DB 准确性对比表")

    _write_xlsx(output_path, sheets)
    return output_path


def _build_direct_sheets(payload: dict[str, Any], source_path: Path) -> list[SheetSpec]:
    tables = [table for table in payload.get("tables", []) if isinstance(table, dict)]
    differences = _flatten_direct_differences(tables)
    reason_counts = Counter(row[1] for row in differences)
    field_counts = Counter(row[2] for row in differences)
    table_rows = [
        [
            _to_text(table.get("table")),
            "是" if table.get("passed") else "否",
            _to_text(table.get("windows_checked")),
            _to_text(table.get("db_rows_checked")),
            _to_text(table.get("source_rows_checked")),
            _to_text(len(table.get("differences") or [])),
        ]
        for table in tables
    ]

    summary_rows: list[list[Any]] = [
        ["项目", "值"],
        ["源JSON", str(source_path)],
        ["整体是否通过", "是" if payload.get("passed") else "否"],
        ["表数量", len(tables)],
        ["窗口总数", sum(_to_int(table.get("windows_checked")) for table in tables)],
        ["DB校验行数", sum(_to_int(table.get("db_rows_checked")) for table in tables)],
        ["源校验行数", sum(_to_int(table.get("source_rows_checked")) for table in tables)],
        ["差异总数", len(differences)],
        [],
        DIRECT_TABLE_HEADER,
        *table_rows,
    ]

    sheets = [
        SheetSpec("汇总", summary_rows, [22, 90, 16, 16, 16, 16], freeze_header=True, autofilter=False)
    ]
    sheets.extend(_detail_sheets(differences))
    sheets.append(
        SheetSpec(
            "按异常类型",
            [["异常类型", "数量"], *[[reason, count] for reason, count in reason_counts.most_common()]],
            [34, 14],
        )
    )
    sheets.append(
        SheetSpec(
            "按异常字段",
            [["异常字段", "数量"], *[[field, count] for field, count in field_counts.most_common()]],
            [34, 14],
        )
    )
    return sheets


def _build_cached_sheets(payload: dict[str, Any], source_path: Path) -> list[SheetSpec]:
    shards = [shard for shard in payload.get("shards", []) if isinstance(shard, dict)]
    summary_rows: list[list[Any]] = [
        ["项目", "值"],
        ["源JSON", str(source_path)],
        ["整体是否通过", "是" if payload.get("passed") else "否"],
        ["分片数量", len(shards)],
        ["DB校验行数", sum(_to_int(shard.get("db_rows")) for shard in shards)],
        ["源校验行数", sum(_to_int(shard.get("source_rows")) for shard in shards)],
        ["差异总数", sum(_to_int(shard.get("differences")) for shard in shards)],
    ]
    shard_rows = [
        [
            _to_text(shard.get("shard_label")),
            _to_text(shard.get("partition_label")),
            _to_text(shard.get("status")),
            _to_text(shard.get("db_rows")),
            _to_text(shard.get("source_rows")),
            _to_text(shard.get("differences")),
            _to_text(shard.get("report_path")),
            _to_text(shard.get("diff_path")),
            _to_text(shard.get("message")),
        ]
        for shard in shards
    ]
    return [
        SheetSpec("汇总", summary_rows, [22, 90], freeze_header=True, autofilter=False),
        SheetSpec("分片结果", [CACHED_SHARD_HEADER, *shard_rows], [34, 28, 16, 14, 14, 14, 54, 54, 80]),
    ]


def _flatten_direct_differences(tables: Sequence[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for table in tables:
        table_name = _to_text(table.get("table"))
        for difference in table.get("differences") or []:
            if not isinstance(difference, dict):
                continue
            diff_table = _to_text(difference.get("table")) or table_name
            reason = _to_text(difference.get("reason"))
            field = _to_text(difference.get("field"))
            row_key = _to_text(difference.get("row_key"))
            key_label = _to_text(difference.get("key_label"))
            db_value = _to_text(difference.get("db_value"))
            source_value = _to_text(difference.get("source_value"))
            db_time, source_time = _derive_utc_times(difference)
            rows.append(
                [
                    diff_table,
                    reason,
                    field,
                    row_key,
                    key_label,
                    db_value,
                    source_value,
                    db_time,
                    source_time,
                    _describe_difference(reason, field),
                ]
            )
    return rows


def _detail_sheets(rows: Sequence[Sequence[str]]) -> list[SheetSpec]:
    widths = [34, 28, 24, 24, 38, 36, 36, 22, 22, 70]
    if not rows:
        return [SheetSpec("对比结果", [DETAIL_HEADER], widths)]

    max_data_rows = EXCEL_MAX_ROWS - 1
    sheets: list[SheetSpec] = []
    for index, start in enumerate(range(0, len(rows), max_data_rows), start=1):
        chunk = rows[start : start + max_data_rows]
        sheet_name = "对比结果" if len(rows) <= max_data_rows else f"对比结果_{index}"
        sheets.append(SheetSpec(sheet_name, [DETAIL_HEADER, *chunk], widths))
    return sheets


def _derive_utc_times(difference: dict[str, Any]) -> tuple[str, str]:
    reason = _to_text(difference.get("reason"))
    field = _to_text(difference.get("field")).lower()
    row_timestamp = _extract_timestamp(difference.get("row_key"))
    if row_timestamp is None:
        row_timestamp = _extract_timestamp(difference.get("key_label"))

    db_time = _format_utc_ms(row_timestamp)
    source_time = db_time
    if field in TIMESTAMP_FIELDS:
        db_time = _format_utc_ms(_extract_timestamp(difference.get("db_value"))) or db_time
        source_time = _format_utc_ms(_extract_timestamp(difference.get("source_value"))) or source_time

    if reason == "missing_db_row":
        return "", source_time
    if reason == "missing_source_row":
        return db_time, ""
    if reason in {"duplicate_db_row_key", "null_db_row_key", "missing_db_row_key_field"}:
        return db_time, ""
    if reason in {"duplicate_source_row_key", "null_source_row_key"}:
        return "", source_time
    return db_time, source_time


def _extract_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if _looks_like_timestamp(value) else None
    text = str(value).strip()
    if INTEGER_PATTERN.match(text):
        try:
            number = int(text)
        except ValueError:
            return None
        return number if _looks_like_timestamp(number) else None

    match = TIMESTAMP_PATTERN.search(text)
    if not match:
        return None
    number = int(match.group(1))
    return number if _looks_like_timestamp(number) else None


def _looks_like_timestamp(value: int) -> bool:
    abs_value = abs(value)
    return 1_000_000_000 <= abs_value <= 99_999_999_999_999


def _format_utc_ms(value: int | None) -> str:
    if value is None:
        return ""
    timestamp = value / 1000 if abs(value) >= 100_000_000_000 else value
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


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
    if reason.startswith("window_error:"):
        return "该时间窗口请求或对比失败，请结合异常类型中的错误信息定位接口、网络或数据格式问题。"
    if reason.startswith("window_planning_error:"):
        return "该 key 的时间窗口规划失败，请检查 DB 时间范围和表配置。"
    if reason.startswith("table_error:"):
        return "表级校验失败，请结合异常类型中的错误信息定位配置或读取问题。"
    if reason.startswith("unknown_table:"):
        return "命令指定的表未出现在 DB accuracy 配置中。"
    if reason in notes:
        return notes[reason]
    if reason:
        return f"未归类异常；字段 {field or '-'} 的异常类型为 {reason}。"
    return "未提供异常类型，请检查源 JSON。"


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_xlsx(output_path: Path, sheets: Sequence[SheetSpec]) -> None:
    safe_sheets = _deduplicate_sheet_names(sheets)
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(len(safe_sheets)))
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("docProps/core.xml", _core_props_xml(created))
        zf.writestr("docProps/app.xml", _app_props_xml([sheet.name for sheet in safe_sheets]))
        zf.writestr("xl/workbook.xml", _workbook_xml([sheet.name for sheet in safe_sheets]))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(safe_sheets)))
        zf.writestr("xl/styles.xml", _styles_xml())
        for index, sheet in enumerate(safe_sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(sheet))


def _deduplicate_sheet_names(sheets: Sequence[SheetSpec]) -> list[SheetSpec]:
    used: set[str] = set()
    result: list[SheetSpec] = []
    for sheet in sheets:
        base = _sanitize_sheet_name(sheet.name)
        name = base
        suffix = 2
        while name in used:
            ending = f"_{suffix}"
            name = f"{base[:31 - len(ending)]}{ending}"
            suffix += 1
        used.add(name)
        result.append(
            SheetSpec(
                name=name,
                rows=sheet.rows,
                widths=sheet.widths,
                freeze_header=sheet.freeze_header,
                autofilter=sheet.autofilter,
            )
        )
    return result


def _sanitize_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", name).strip("'") or "Sheet"
    return cleaned[:31]


def _worksheet_xml(sheet: SheetSpec) -> str:
    max_cols = max((len(row) for row in sheet.rows), default=1)
    max_rows = max(len(sheet.rows), 1)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        f'<dimension ref="A1:{_column_name(max_cols)}{max_rows}"/>',
    ]
    if sheet.freeze_header:
        parts.append(
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
            "</sheetView></sheetViews>"
        )
    parts.append(_cols_xml(sheet.widths, max_cols))
    parts.append("<sheetData>")
    for row_index, row in enumerate(sheet.rows, start=1):
        parts.append(f'<row r="{row_index}">')
        for col_index, value in enumerate(row, start=1):
            ref = f"{_column_name(col_index)}{row_index}"
            style_id = 1 if row_index == 1 else 0
            parts.append(_cell_xml(ref, value, style_id))
        parts.append("</row>")
    parts.append("</sheetData>")
    if sheet.autofilter and max_rows >= 1 and max_cols >= 1:
        parts.append(f'<autoFilter ref="A1:{_column_name(max_cols)}{max_rows}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def _cols_xml(widths: Sequence[int], max_cols: int) -> str:
    if max_cols <= 0:
        return ""
    parts = ["<cols>"]
    for col in range(1, max_cols + 1):
        width = widths[col - 1] if col - 1 < len(widths) else 18
        parts.append(f'<col min="{col}" max="{col}" width="{width}" customWidth="1"/>')
    parts.append("</cols>")
    return "".join(parts)


def _cell_xml(ref: str, value: Any, style_id: int) -> str:
    text = _escape_text(_to_text(value))
    return f'<c r="{ref}" s="{style_id}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _escape_text(value: str) -> str:
    return escape(INVALID_XML_CHARS.sub("", value))


def _escape_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name or "A"


def _content_types_xml(sheet_count: int) -> str:
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    overrides.extend(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'{"".join(overrides)}'
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheet_names: Sequence[str]) -> str:
    sheets_xml = "".join(
        f'<sheet name="{_escape_attr(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f"{sheets_xml}"
        "</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    relationships = [
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    ]
    relationships.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(relationships)}'
        "</Relationships>"
    )


def _core_props_xml(created: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>QuestTest</dc:creator>"
        "<cp:lastModifiedBy>QuestTest</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_props_xml(sheet_names: Sequence[str]) -> str:
    headings = (
        '<HeadingPairs><vt:vector size="2" baseType="variant">'
        '<vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>'
        f"<vt:variant><vt:i4>{len(sheet_names)}</vt:i4></vt:variant>"
        "</vt:vector></HeadingPairs>"
    )
    titles = "".join(f"<vt:lpstr>{_escape_text(name)}</vt:lpstr>" for name in sheet_names)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>QuestTest</Application>"
        f"{headings}"
        f'<TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{titles}</vt:vector></TitlesOfParts>'
        "</Properties>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="@"/></numFmts>'
        '<fonts count="2">'
        '<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        "</fonts>"
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFBDD7EE"/><bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="164" fontId="1" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        "</styleSheet>"
    )


def _infer_output_slug(payload: dict[str, Any]) -> str:
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        names = [_to_text(table.get("table")) for table in tables if isinstance(table, dict)]
        if len(names) == 1 and names[0]:
            return _slugify(names[0])
        return "multi_tables"
    if isinstance(payload.get("shards"), list):
        return "cached_shards"
    return "unknown"


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_")
    return cleaned or "table"


def build_default_output_paths(
    payload: dict[str, Any],
    reports_dir: Path,
    *,
    source_path: Path | None = None,
) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _infer_output_slug(payload)
    output_dir = _infer_output_dir(payload, Path(reports_dir), source_path)
    stamped = output_dir / f"db_accuracy_allure_{slug}_{stamp}_zh.xlsx"
    latest = output_dir / "db_accuracy_allure_latest_zh.xlsx"
    return stamped, latest


def _infer_output_dir(
    payload: dict[str, Any],
    reports_dir: Path,
    source_path: Path | None,
) -> Path:
    if source_path is not None:
        try:
            relative = source_path.parent.resolve().relative_to(DEFAULT_DB_ACCURACY_ALLURE_ROOT.resolve())
        except ValueError:
            pass
        else:
            return reports_dir / "db_accuracy" / relative

    return reports_dir / "db_accuracy" / _infer_output_slug(payload)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert DB accuracy Allure JSON attachments to a Chinese xlsx report.")
    parser.add_argument("--input", type=Path, help="指定 db_accuracy_details JSON 附件路径；不传则自动读取最新 Allure 附件")
    parser.add_argument("--allure-dir", type=Path, default=DEFAULT_ALLURE_DIR, help="Allure raw results 目录")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR, help="默认输出目录")
    parser.add_argument("--output", type=Path, help="指定 xlsx 输出路径；不传则写入 reports 下的时间戳文件和 latest 文件")
    parser.add_argument("--no-latest", action="store_true", help="使用默认输出时，不额外覆盖 latest 文件")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source_path = args.input or find_latest_accuracy_attachment(args.allure_dir)
    payload = load_json_payload(source_path)
    if not is_accuracy_payload(payload):
        raise SystemExit(f"{source_path} 不是 DB accuracy 的 Allure JSON 附件")

    if args.output:
        output_path = args.output
        latest_path = None
    else:
        output_path, latest_path = build_default_output_paths(
            payload,
            args.reports_dir,
            source_path=source_path,
        )

    write_accuracy_workbook(payload, source_path=source_path, output_path=output_path)
    print(f"xlsx={output_path}")
    if latest_path is not None and not args.no_latest:
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, latest_path)
        print(f"latest={latest_path}")
    print(f"source_json={source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
