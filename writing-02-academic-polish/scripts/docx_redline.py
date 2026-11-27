#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
docx_redline.py - Pure Python Standard-Library OOXML (.docx) Generator
with Clean Manuscript & Real Word Track Changes (<w:del>/<w:ins>) + Audit Ledger Table.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import datetime
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import zipfile

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>
"""

_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOCUMENT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>
"""

_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="SimSun" w:cs="Calibri"/>
        <w:sz w:val="22"/>
        <w:szCs w:val="22"/>
      </w:rPr>
    </w:rPrDefault>
    <w:pPrDefault>
      <w:pPr>
        <w:spacing w:after="120" w:line="276" w:lineRule="auto"/>
      </w:pPr>
    </w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="120" w:after="200"/></w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>
      <w:b/>
      <w:sz w:val="32"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:keepNext/><w:spacing w:before="220" w:after="100"/></w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>
      <w:b/>
      <w:sz w:val="28"/>
      <w:color w:val="1B365D"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:keepNext/><w:spacing w:before="180" w:after="80"/></w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>
      <w:b/>
      <w:sz w:val="24"/>
      <w:color w:val="2B6CB0"/>
    </w:rPr>
  </w:style>
</w:styles>
"""


def _settings_xml(track_revisions=False):
    tr_tag = "<w:trackRevisions/>" if track_revisions else ""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  {tr_tag}
  <w:defaultTabStop w:val="720"/>
  <w:characterSpacingControl w:val="doNotCompress"/>
</w:settings>
"""


def _clean_xml(text):
    return xml_escape(_ILLEGAL_XML_RE.sub("", str(text or "")))


def _w_run(text, bold=False, italic=False, color=None, size_half_pt=None, is_del=False):
    safe = _clean_xml(text)
    rpr_parts = []
    if bold:
        rpr_parts.append("<w:b/>")
    if italic:
        rpr_parts.append("<w:i/>")
    if color:
        rpr_parts.append(f'<w:color w:val="{color}"/>')
    elif is_del:
        rpr_parts.append('<w:color w:val="B91C1C"/>')
    if size_half_pt:
        rpr_parts.append(f'<w:sz w:val="{int(size_half_pt)}"/>')
    rpr = f"<w:rPr>{''.join(rpr_parts)}</w:rPr>" if rpr_parts else ""
    tag = "w:delText" if is_del else "w:t"
    return f'<w:r>{rpr}<{tag} xml:space="preserve">{safe}</{tag}></w:r>'


def _w_para(runs_xml, style_id=None, shading=None):
    ppr_parts = []
    if style_id:
        ppr_parts.append(f'<w:pStyle w:val="{_clean_xml(style_id)}"/>')
    if shading:
        ppr_parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shading}"/>')
    ppr = f"<w:pPr>{''.join(ppr_parts)}</w:pPr>" if ppr_parts else ""
    return f"<w:p>{ppr}{runs_xml}</w:p>"


def _w_table(headers, rows, col_widths=None):
    n_cols = len(headers)
    if not col_widths:
        col_widths = [int(9000 / max(n_cols, 1))] * n_cols
    tbl_pr = (
        '<w:tblPr>'
        '<w:tblW w:w="9000" w:type="dxa"/>'
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>'
        '</w:tblBorders>'
        '</w:tblPr>'
    )
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in col_widths) + "</w:tblGrid>"

    def make_row(cells, is_header=False):
        tr_cells = []
        for idx, ctext in enumerate(cells):
            w_val = col_widths[idx] if idx < len(col_widths) else col_widths[-1]
            shd = '<w:shd w:val="clear" w:color="auto" w:fill="F1F5F9"/>' if is_header else ""
            tc_pr = f'<w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/>{shd}</w:tcPr>'
            run = _w_run(ctext, bold=is_header, size_half_pt=18)
            tr_cells.append(f"<w:tc>{tc_pr}{_w_para(run)}</w:tc>")
        return "<w:tr>" + "".join(tr_cells) + "</w:tr>"

    row_xml = [make_row(headers, is_header=True)]
    for r in rows:
        row_xml.append(make_row(r, is_header=False))
    return f"<w:tbl>{tbl_pr}{grid}{''.join(row_xml)}</w:tbl>"


def _write_docx_zip(out_path, document_xml, track_revisions=False):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES_XML.encode("utf-8"))
        z.writestr("_rels/.rels", _RELS_XML.encode("utf-8"))
        z.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS_XML.encode("utf-8"))
        z.writestr("word/styles.xml", _STYLES_XML.encode("utf-8"))
        z.writestr("word/settings.xml", _settings_xml(track_revisions=track_revisions).encode("utf-8"))
        z.writestr("word/document.xml", document_xml.encode("utf-8"))


def _block_style_id(blk):
    btype = blk.get("block_type", "paragraph")
    if btype == "heading":
        # Infer heading level from prefix if markdown
        pref = blk.get("block_prefix", "")
        hashes = len(pref.strip()) if pref.strip().startswith("#") else 1
        return "Heading1" if hashes <= 1 else "Heading2"
    return "Normal"


def generate_clean_docx(out_path, title, blocks, sentence_map, metadata=None):
    """Generate clean submission-ready DOCX from polished sentences & blocks."""
    metadata = metadata or {}
    body_elems = []
    if title:
        body_elems.append(_w_para(_w_run(title, bold=True), style_id="Title"))
    subtitle_parts = []
    if metadata.get("target_style"):
        subtitle_parts.append(f"Style: {metadata['target_style']}")
    if metadata.get("edit_intensity"):
        subtitle_parts.append(f"Intensity: {metadata['edit_intensity']}")
    if subtitle_parts:
        body_elems.append(_w_para(_w_run(" | ".join(subtitle_parts), italic=True, color="475569", size_half_pt=18)))

    for blk in (blocks or []):
        btype = blk.get("block_type", "paragraph")
        if btype in ("whitespace", "latex_preamble", "latex_epilogue", "latex_comment", "latex_env_marker"):
            continue
        if btype == "table" and "|" in blk.get("body_text", ""):
            lines = [ln.strip() for ln in blk["body_text"].splitlines() if ln.strip() and not re.match(r"^[\s|:-]+$", ln)]
            if lines:
                parsed_rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
                headers = parsed_rows[0]
                rows = [r + [""] * (len(headers) - len(r)) for r in parsed_rows[1:]]
                body_elems.append(_w_table(headers, rows))
                continue

        slots = blk.get("sentence_slots")
        if slots:
            parts = []
            for slot in slots:
                sid = slot.get("sentence_id")
                txt = sentence_map.get(sid, slot.get("source_text", "")) if sid else slot.get("source_text", "")
                parts.append(slot.get("prefix_markup", "") + txt + slot.get("suffix_markup", "") + slot.get("separator_after", ""))
            para_txt = "".join(parts).strip()
        else:
            para_txt = (blk.get("body_text") or "").strip()

        if para_txt:
            style_id = _block_style_id(blk)
            body_elems.append(_w_para(_w_run(para_txt), style_id=style_id))

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_elems)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    _write_docx_zip(out_path, doc_xml, track_revisions=False)
    return out_path


def generate_redline_docx(out_path, title, blocks, sentence_records, audit_summary=None, metadata=None):
    """
    Generate Word Track Changes (.docx) with <w:del> and <w:ins> elements from diff_ops,
    followed by an Audit Summary & Sentence-by-Sentence Revision Ledger table.
    """
    metadata = metadata or {}
    audit_summary = audit_summary or {}
    rec_by_id = {r["sentence_id"]: r for r in (sentence_records or []) if r.get("sentence_id")}
    ts_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    author = "Academic Language Polish Skill"
    rev_id = 1

    def next_rev_id():
        nonlocal rev_id
        rid = rev_id
        rev_id += 1
        return rid

    body_elems = []
    if title:
        body_elems.append(_w_para(_w_run(f"{title} [Track Changes Redline]", bold=True), style_id="Title"))

    for blk in (blocks or []):
        btype = blk.get("block_type", "paragraph")
        if btype in ("whitespace", "latex_preamble", "latex_epilogue", "latex_comment", "latex_env_marker"):
            continue
        if btype == "table" and "|" in blk.get("body_text", ""):
            lines = [ln.strip() for ln in blk["body_text"].splitlines() if ln.strip() and not re.match(r"^[\s|:-]+$", ln)]
            if lines:
                parsed_rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
                headers = parsed_rows[0]
                rows = [r + [""] * (len(headers) - len(r)) for r in parsed_rows[1:]]
                body_elems.append(_w_table(headers, rows))
                continue

        slots = blk.get("sentence_slots")
        if slots:
            runs = []
            for slot in slots:
                if slot.get("prefix_markup"):
                    runs.append(_w_run(slot["prefix_markup"]))
                sid = slot.get("sentence_id")
                rec = rec_by_id.get(sid) if sid else None
                if rec and rec.get("diff") and rec["diff"].get("diff_ops"):
                    for op in rec["diff"]["diff_ops"]:
                        kind = op.get("op")
                        if kind == "equal":
                            if op.get("pol"):
                                runs.append(_w_run(op["pol"]))
                        elif kind == "delete":
                            if op.get("src"):
                                rid = next_rev_id()
                                runs.append(f'<w:del w:id="{rid}" w:author="{author}" w:date="{ts_iso}">{_w_run(op["src"], is_del=True)}</w:del>')
                        elif kind == "insert":
                            if op.get("pol"):
                                rid = next_rev_id()
                                runs.append(f'<w:ins w:id="{rid}" w:author="{author}" w:date="{ts_iso}">{_w_run(op["pol"], color="15803D")}</w:ins>')
                        elif kind == "replace":
                            if op.get("src"):
                                rid = next_rev_id()
                                runs.append(f'<w:del w:id="{rid}" w:author="{author}" w:date="{ts_iso}">{_w_run(op["src"], is_del=True)}</w:del>')
                            if op.get("pol"):
                                rid = next_rev_id()
                                runs.append(f'<w:ins w:id="{rid}" w:author="{author}" w:date="{ts_iso}">{_w_run(op["pol"], color="15803D")}</w:ins>')
                else:
                    runs.append(_w_run(slot.get("source_text", "")))
                tail = slot.get("suffix_markup", "") + slot.get("separator_after", "")
                if tail:
                    runs.append(_w_run(tail))
            if runs:
                body_elems.append(_w_para("".join(runs), style_id=_block_style_id(blk)))
        else:
            txt = (blk.get("body_text") or "").strip()
            if txt:
                body_elems.append(_w_para(_w_run(txt), style_id=_block_style_id(blk)))

    # Append Audit Summary & Sentence-by-Sentence Revision Ledger
    body_elems.append(_w_para(_w_run("Audit Summary & Sentence-by-Sentence Revision Ledger", bold=True), style_id="Heading1"))
    sum_line = (
        f"Total Sentences: {audit_summary.get('total_sentences', len(sentence_records or []))} | "
        f"Edited: {audit_summary.get('edited_sentences', 0)} | "
        f"Unchanged: {audit_summary.get('unchanged_sentences', 0)} | "
        f"Mean Edit Ratio: {audit_summary.get('mean_edit_ratio', 0.0)} | "
        f"Overall Audit Score: {audit_summary.get('overall_audit_score', 'N/A')} | "
        f"HIGH Issues: {audit_summary.get('high_count', 0)}"
    )
    body_elems.append(_w_para(_w_run(sum_line, color="334155", size_half_pt=20)))

    ledger_rows = []
    for rec in (sentence_records or []):
        ledger_rows.append([
            rec.get("sentence_id", ""),
            rec.get("source_text", ""),
            rec.get("polished_text", ""),
            ", ".join(rec.get("edit_types") or []),
            rec.get("reason", "") or ("Preserved verbatim." if rec.get("source_text") == rec.get("polished_text") else ""),
        ])
    if ledger_rows:
        body_elems.append(_w_table(
            ["ID", "Original", "Polished", "Edit Types", "Reason"],
            ledger_rows,
            col_widths=[700, 2600, 2600, 1300, 1800],
        ))

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_elems)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    _write_docx_zip(out_path, doc_xml, track_revisions=True)
    return out_path


def verify_docx_package(docx_path, expect_track_changes=False):
    """
    Validate OOXML ZIP structure, parse all XML parts via ElementTree,
    and verify <w:del>/<w:ins> nodes when expect_track_changes=True.
    """
    required_parts = {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/settings.xml",
    }
    report = {
        "valid": False,
        "path": docx_path,
        "missing_parts": [],
        "xml_parse_errors": [],
        "paragraph_count": 0,
        "table_count": 0,
        "del_count": 0,
        "ins_count": 0,
    }
    if not os.path.isfile(docx_path):
        report["xml_parse_errors"].append("File does not exist.")
        return report

    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            names = set(z.namelist())
            report["missing_parts"] = sorted(required_parts - names)
            if report["missing_parts"]:
                return report

            for name in sorted(names):
                if name.endswith(".xml") or name.endswith(".rels"):
                    try:
                        root = ET.fromstring(z.read(name))
                        if name == "word/document.xml":
                            w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                            report["paragraph_count"] = len(root.findall(f".//{w_ns}p"))
                            report["table_count"] = len(root.findall(f".//{w_ns}tbl"))
                            report["del_count"] = len(root.findall(f".//{w_ns}del"))
                            report["ins_count"] = len(root.findall(f".//{w_ns}ins"))
                    except Exception as xe:
                        report["xml_parse_errors"].append(f"{name}: {xe}")
    except Exception as ze:
        report["xml_parse_errors"].append(str(ze))
        return report

    if report["xml_parse_errors"]:
        return report
    if expect_track_changes and (report["del_count"] + report["ins_count"]) == 0:
        report["xml_parse_errors"].append("Expected <w:del>/<w:ins> track-change nodes, found 0.")
        return report

    report["valid"] = True
    return report
