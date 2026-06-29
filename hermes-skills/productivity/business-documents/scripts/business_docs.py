#!/usr/bin/env python3
"""Generate lightweight business .docx and .csv artifacts for Hermes.

No third-party packages are required. The DOCX writer emits a small but valid
Office Open XML package; the CSV writer targets Russian Excel defaults.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = CONTROL_CHARS.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def xml_escape(value: Any) -> str:
    return escape(clean_text(value), {'"': "&quot;"})


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def default_output_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes" / "artifacts" / "business-documents"
    return Path.home() / ".hermes" / "artifacts" / "business-documents"


def slugify(value: str, fallback: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^0-9a-zа-яё._-]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("._-")
    return (value or fallback)[:64]


def output_path(out_dir: Path, filename: str | None, title: str, prefix: str, ext: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if filename:
        safe = slugify(Path(filename).stem, prefix)
        return out_dir / f"{safe}.{ext}"
    return out_dir / f"{prefix}_{slugify(title, prefix)}_{timestamp}.{ext}"


def load_spec(path: str | None, brief: str | None, kind: str) -> dict[str, Any]:
    if path:
        with open(path, "r", encoding="utf-8-sig") as fh:
            spec = json.load(fh)
        if not isinstance(spec, dict):
            raise ValueError("Spec JSON must be an object")
        return spec
    if not brief:
        raise ValueError("Provide --spec or --brief")
    return fallback_spec(brief, kind)


def fallback_spec(brief: str, kind: str) -> dict[str, Any]:
    brief = clean_text(brief)
    if kind == "proposal":
        return {
            "title": "Коммерческое предложение",
            "client": "Клиент",
            "subject": brief,
            "summary": f"Предложение подготовлено по запросу: {brief}",
            "sections": [
                {
                    "heading": "Цель",
                    "paragraphs": [
                        "Сформировать понятный результат под задачу клиента и довести его до рабочего состояния."
                    ],
                },
                {
                    "heading": "Что входит",
                    "paragraphs": [
                        "Анализ задачи, подготовка решения, внедрение, проверка результата и короткая инструкция по использованию."
                    ],
                },
                {
                    "heading": "Результат",
                    "paragraphs": [
                        "Клиент получает готовый рабочий артефакт, который можно проверить на реальном сценарии."
                    ],
                },
            ],
            "pricing": [
                {"item": "Работы по запросу", "qty": "1 проект", "price": "по согласованию", "total": "по согласованию"}
            ],
            "terms": ["Срок и стоимость уточняются после согласования финального объёма."],
            "contacts": ["Подготовлено персональным агентом Hermes."],
        }
    return {
        "title": "Отчёт",
        "sections": [
            {
                "heading": "Краткое описание",
                "paragraphs": [f"Отчёт создан на основе запроса: {brief}"],
            },
            {
                "heading": "Сводная таблица",
                "table": {
                    "columns": ["Раздел", "Статус", "Комментарий"],
                    "rows": [
                        ["Запрос", "Принято", brief],
                        ["Следующий шаг", "К выполнению", "Уточнить метрики, сроки и ответственных при необходимости."],
                    ],
                },
            },
            {
                "heading": "Примечания",
                "bullets": ["Отчёт создан на основе краткого запроса пользователя."],
            },
        ],
    }


def ensure_meaningful(spec: dict[str, Any]) -> None:
    text = json.dumps(spec, ensure_ascii=False)
    words = [w for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё]{3,}", text) if w.lower() not in {"null", "true", "false"}]
    if len(set(words)) < 8:
        raise ValueError("Spec is too sparse; draft real document content before generating the file")
    lowered = text.lower()
    blocked = ("lorem ipsum", "todo", "заглушка", "рыба текста")
    if any(token in lowered for token in blocked):
        raise ValueError("Spec contains placeholder text; replace it with real content")


def paragraph(text: Any = "", style: str | None = None, *, bold: bool = False) -> str:
    text = clean_text(text)
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    if not text:
        return f"<w:p>{ppr}</w:p>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r></w:p>'


def paragraphs(items: Any, style: str | None = None) -> list[str]:
    output: list[str] = []
    for item in as_list(items):
        for line in clean_text(item).split("\n"):
            output.append(paragraph(line, style))
    return output


def heading(text: Any, level: int = 1) -> str:
    return paragraph(text, f"Heading{min(max(level, 1), 2)}")


def bullet(text: Any) -> str:
    return paragraph(f"• {clean_text(text)}")


def table_xml(rows: list[list[Any]], header: bool = True) -> str:
    if not rows:
        return ""
    col_count = max(len(row) for row in rows)
    col_width = max(1200, int(9000 / col_count))
    grid = "".join(f'<w:gridCol w:w="{col_width}"/>' for _ in range(col_count))
    border = 'w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"'
    borders = (
        f"<w:tblBorders><w:top {border}/><w:left {border}/><w:bottom {border}/>"
        f"<w:right {border}/><w:insideH {border}/><w:insideV {border}/></w:tblBorders>"
    )
    xml = [
        "<w:tbl>",
        f'<w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}</w:tblPr>',
        f"<w:tblGrid>{grid}</w:tblGrid>",
    ]
    for row_index, row in enumerate(rows):
        xml.append("<w:tr>")
        for cell in list(row) + [""] * (col_count - len(row)):
            shade = '<w:shd w:fill="F2F2F2"/>' if header and row_index == 0 else ""
            xml.append(f'<w:tc><w:tcPr><w:tcW w:w="{col_width}" w:type="dxa"/>{shade}</w:tcPr>')
            xml.append(paragraph(cell, bold=header and row_index == 0))
            xml.append("</w:tc>")
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


def normalize_pricing(pricing: Any) -> list[list[Any]]:
    rows = [["Позиция", "Кол-во", "Цена", "Сумма"]]
    for item in as_list(pricing):
        if isinstance(item, dict):
            rows.append([
                item.get("item") or item.get("name") or "",
                item.get("qty") or item.get("quantity") or "",
                item.get("price") or "",
                item.get("total") or item.get("sum") or "",
            ])
        else:
            rows.append([item, "", "", ""])
    return rows if len(rows) > 1 else []


def normalize_report_rows(spec: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    rows = as_list(spec.get("rows"))
    columns = as_list(spec.get("columns"))
    if not rows:
        raise ValueError("Report spec must contain rows")
    if rows and isinstance(rows[0], dict):
        if not columns:
            seen: list[str] = []
            for row in rows:
                for key in row.keys():
                    if key not in seen:
                        seen.append(str(key))
            columns = seen
        normalized = [[row.get(col, "") for col in columns] for row in rows]
    else:
        normalized = [as_list(row) for row in rows]
        if not columns:
            columns = [f"Колонка {i + 1}" for i in range(max(len(row) for row in normalized))]
    return [str(col) for col in columns], normalized


def normalize_table(columns: Any, rows: Any) -> tuple[list[str], list[list[Any]]]:
    raw_rows = as_list(rows)
    raw_columns = as_list(columns)
    if raw_rows and isinstance(raw_rows[0], dict):
        if not raw_columns:
            seen: list[str] = []
            for row in raw_rows:
                for key in row.keys():
                    key = str(key)
                    if key not in seen:
                        seen.append(key)
            raw_columns = seen
        normalized = [[row.get(col, "") for col in raw_columns] for row in raw_rows]
    else:
        normalized = [as_list(row) for row in raw_rows]
        if not raw_columns and normalized:
            raw_columns = [f"Колонка {i + 1}" for i in range(max(len(row) for row in normalized))]
    return [str(col) for col in raw_columns], normalized


def csv_delimiter(spec: dict[str, Any], default: str) -> str:
    raw = clean_text(spec.get("delimiter") or spec.get("separator") or default).lower()
    aliases = {
        "comma": ",",
        "запятая": ",",
        ",": ",",
        "semicolon": ";",
        "точка с запятой": ";",
        ";": ";",
        "tab": "\t",
        "tsv": "\t",
        "\\t": "\t",
    }
    return aliases.get(raw, default)


def section_tables(section: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(section.get("table"), dict):
        tables.append(section["table"])
    for table in as_list(section.get("tables")):
        if isinstance(table, dict):
            tables.append(table)
    if section.get("columns") or section.get("rows"):
        tables.append({"columns": section.get("columns"), "rows": section.get("rows")})
    return tables


def report_layout_width(spec: dict[str, Any]) -> int:
    width = int(spec.get("layout_columns") or 3)
    for section in as_list(spec.get("sections")):
        if not isinstance(section, dict):
            continue
        for table in section_tables(section):
            columns, rows = normalize_table(table.get("columns"), table.get("rows"))
            width = max(width, len(columns), *(len(row) for row in rows), 3)
    if spec.get("columns") or spec.get("rows"):
        columns, rows = normalize_report_rows(spec)
        width = max(width, len(columns), *(len(row) for row in rows), 3)
    return width


def padded_row(values: list[Any], width: int) -> list[str]:
    row = [clean_text(value) for value in values]
    if len(row) < width:
        row.extend([""] * (width - len(row)))
    return row


def write_pretty_csv(spec: dict[str, Any], out_file: Path) -> None:
    width = report_layout_width(spec)
    delimiter = csv_delimiter(spec, default=",")
    with open(out_file, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter)

        def emit(values: list[Any] | None = None) -> None:
            writer.writerow(padded_row(values or [], width))

        title = clean_text(spec.get("title") or "Отчёт")
        emit([title])
        emit()

        if spec.get("summary"):
            for paragraph_text in as_list(spec.get("summary")):
                for line in clean_text(paragraph_text).split("\n"):
                    emit([line])
            emit()

        for raw_section in as_list(spec.get("sections")):
            if isinstance(raw_section, str):
                emit([raw_section])
                emit()
                continue
            if not isinstance(raw_section, dict):
                continue

            if raw_section.get("heading"):
                emit([raw_section["heading"]])

            for paragraph_text in as_list(raw_section.get("paragraphs") or raw_section.get("body")):
                for line in clean_text(paragraph_text).split("\n"):
                    emit([line])

            if raw_section.get("items"):
                for item in as_list(raw_section.get("items")):
                    emit([f"•  {clean_text(item)}"])

            if raw_section.get("bullets"):
                for item in as_list(raw_section.get("bullets")):
                    emit([f"•  {clean_text(item)}"])

            for table in section_tables(raw_section):
                if table.get("caption"):
                    emit([table["caption"]])
                columns, rows = normalize_table(table.get("columns"), table.get("rows"))
                if columns:
                    emit(columns)
                for row in rows:
                    emit(row)

            emit()

        notes = as_list(spec.get("notes"))
        if notes:
            emit(["Важные замечания"])
            for note in notes:
                emit([f"•  {clean_text(note)}"])


def build_proposal_document(spec: dict[str, Any]) -> str:
    title = spec.get("title") or "Коммерческое предложение"
    parts = [paragraph(title, "Title")]
    meta = []
    if spec.get("client"):
        meta.append(f"Клиент: {clean_text(spec.get('client'))}")
    if spec.get("subject"):
        meta.append(f"Тема: {clean_text(spec.get('subject'))}")
    meta.append(f"Дата: {clean_text(spec.get('date') or datetime.now().strftime('%d.%m.%Y'))}")
    parts.extend(paragraphs(meta, "Meta"))
    parts.append(paragraph())
    if spec.get("summary"):
        parts.append(heading("Кратко", 1))
        parts.extend(paragraphs(spec["summary"]))
    for section in as_list(spec.get("sections")):
        if isinstance(section, dict):
            if section.get("heading"):
                parts.append(heading(section["heading"], 1))
            parts.extend(paragraphs(section.get("paragraphs") or section.get("body") or []))
            for item in as_list(section.get("bullets")):
                parts.append(bullet(item))
        else:
            parts.extend(paragraphs(section))
    pricing = normalize_pricing(spec.get("pricing"))
    if pricing:
        parts.append(heading("Стоимость", 1))
        parts.append(table_xml(pricing, header=True))
    if spec.get("terms"):
        parts.append(heading("Условия", 1))
        for item in as_list(spec.get("terms")):
            parts.append(bullet(item))
    if spec.get("contacts"):
        parts.append(heading("Контакты", 1))
        parts.extend(paragraphs(spec.get("contacts")))
    return document_xml("".join(parts))


def build_styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="240"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="260" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="180" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Meta">
    <w:name w:val="Meta"/>
    <w:basedOn w:val="Normal"/>
    <w:rPr><w:color w:val="666666"/><w:sz w:val="20"/></w:rPr>
  </w:style>
</w:styles>"""


def document_xml(body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>"""


def write_docx(spec: dict[str, Any], out_file: Path) -> None:
    document = build_proposal_document(spec)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    title = xml_escape(spec.get("title") or "Коммерческое предложение")
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "word/_rels/document.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""",
        "word/document.xml": document,
        "word/styles.xml": build_styles_xml(),
        "docProps/core.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{title}</dc:title>
  <dc:creator>Hermes Agent</dc:creator>
  <cp:lastModifiedBy>Hermes Agent</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>""",
        "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Hermes Agent</Application>
</Properties>""",
    }
    with zipfile.ZipFile(out_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content.encode("utf-8"))
    validate_docx(out_file)


def validate_docx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not a zip/docx package")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        missing = required - names
        if missing:
            raise ValueError(f"DOCX package is missing: {', '.join(sorted(missing))}")
        ElementTree.fromstring(zf.read("word/document.xml"))


def write_csv(spec: dict[str, Any], out_file: Path) -> None:
    if spec.get("sections") or spec.get("summary"):
        write_pretty_csv(spec, out_file)
        return

    columns, rows = normalize_report_rows(spec)
    delimiter = csv_delimiter(spec, default=";")
    with open(out_file, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([clean_text(cell) for cell in row])
        notes = as_list(spec.get("notes"))
        if notes:
            writer.writerow([])
            writer.writerow(["Примечания"])
            for note in notes:
                writer.writerow([clean_text(note)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate business DOCX/CSV artifacts for Hermes.")
    parser.add_argument("kind", choices=["proposal", "report"], help="Output type: proposal=.docx, report=.csv")
    parser.add_argument("--spec", help="Path to UTF-8 JSON spec")
    parser.add_argument("--brief", help="Fallback brief used when --spec is not provided")
    parser.add_argument("--out", default=None, help="Output directory; defaults to Hermes artifacts directory")
    parser.add_argument("--filename", default=None, help="Optional output filename")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec, args.brief, args.kind)
    ensure_meaningful(spec)

    out_dir = Path(args.out) if args.out else default_output_dir()
    title = clean_text(spec.get("title") or ("Коммерческое предложение" if args.kind == "proposal" else "Отчёт"))
    if args.kind == "proposal":
        out_file = output_path(out_dir, args.filename, title, "kp", "docx")
        write_docx(spec, out_file)
    else:
        out_file = output_path(out_dir, args.filename, title, "report", "csv")
        write_csv(spec, out_file)

    result = {
        "ok": True,
        "kind": args.kind,
        "path": str(out_file.resolve()),
        "bytes": out_file.stat().st_size,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
