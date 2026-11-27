#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_deliverables.py - Dynamic Output Contract Adapter, Multi-View Verifiable
Deliverables (MD/TeX/CSV/HTML/DOCX) & 4-Tier Visual PDF/PNG Preview Renderer.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import csv
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import case_ingestor
import docx_redline


# ----------------------------------------------------------------------
# 1. Pure Stdlib 5x7 Bitmap Font & PNG Rasterizer (Tier 4 Fallback)
# ----------------------------------------------------------------------

_FONT_5X7 = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x04, 0x04, 0x04, 0x04, 0x00, 0x00, 0x04),
    '"': (0x0A, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00),
    "#": (0x0A, 0x0A, 0x1F, 0x0A, 0x1F, 0x0A, 0x0A),
    "%": (0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03),
    "&": (0x0C, 0x12, 0x14, 0x08, 0x15, 0x12, 0x0D),
    "'": (0x04, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00),
    "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08),
    "*": (0x00, 0x0A, 0x04, 0x1F, 0x04, 0x0A, 0x00),
    "+": (0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00),
    ",": (0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x08),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04),
    "/": (0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10),
    ":": (0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x00),
    ";": (0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x08),
    "=": (0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00),
    "?": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04),
    "[": (0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E),
    "]": (0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E),
    "_": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F),
    "|": (0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "{": (0x02, 0x04, 0x04, 0x08, 0x04, 0x04, 0x02),
    "}": (0x08, 0x04, 0x04, 0x02, 0x04, 0x04, 0x08),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "a": (0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F),
    "b": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x1E),
    "c": (0x00, 0x00, 0x0E, 0x11, 0x10, 0x11, 0x0E),
    "d": (0x01, 0x01, 0x0F, 0x11, 0x11, 0x11, 0x0F),
    "e": (0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E),
    "f": (0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08),
    "g": (0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E),
    "h": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "i": (0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E),
    "j": (0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C),
    "k": (0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12),
    "l": (0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "m": (0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11),
    "n": (0x00, 0x00, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "o": (0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E),
    "p": (0x00, 0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10),
    "q": (0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x01),
    "r": (0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10),
    "s": (0x00, 0x00, 0x0F, 0x10, 0x0E, 0x01, 0x1E),
    "t": (0x08, 0x08, 0x1C, 0x08, 0x08, 0x09, 0x06),
    "u": (0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D),
    "v": (0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "w": (0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A),
    "x": (0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11),
    "y": (0x00, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "z": (0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F),
}


def _ascii_label(txt, max_len=95):
    s = re.sub(r"\s+", " ", str(txt or "")).strip()
    out = []
    for ch in s:
        if ch in _FONT_5X7:
            out.append(ch)
        elif ord(ch) < 128:
            out.append(" ")
        else:
            out.append("*")
    res = "".join(out)
    return res[: max_len - 2] + ".." if len(res) > max_len else res


def _parse_inline_diff_segments(diff_str):
    """Parse '[-del-]{+ins+}' inline_diff into clean (kind, text) segments without raw markers."""
    parts = re.split(r"(\[-.*?-\]|\{\+.*?\+\})", str(diff_str or ""))
    segs = []
    for p in parts:
        if not p:
            continue
        if p.startswith("[-") and p.endswith("-]") and len(p) >= 4:
            segs.append(("del", p[2:-2]))
        elif p.startswith("{+") and p.endswith("+}") and len(p) >= 4:
            segs.append(("ins", p[2:-2]))
        else:
            segs.append(("eq", p))
    return segs


def _render_png_stdlib(summary_info, records, png_path):
    """Tier 4 zero-dependency PNG rasterizer using 5x7 bitmap font + red/green diff blocks."""
    w, h = 1100, 760
    buf = bytearray([255]) * (w * h * 3)

    def fill_rect(x0, y0, x1, y1, rgb):
        rx0, rx1 = max(0, min(w, int(x0))), max(0, min(w, int(x1)))
        ry0, ry1 = max(0, min(h, int(y0))), max(0, min(h, int(y1)))
        if rx1 <= rx0 or ry1 <= ry0:
            return
        row_bytes = bytes(rgb) * (rx1 - rx0)
        stride = w * 3
        for y in range(ry0, ry1):
            off = y * stride + rx0 * 3
            buf[off:off + len(row_bytes)] = row_bytes

    def stroke_rect(x0, y0, x1, y1, rgb):
        fill_rect(x0, y0, x1, y0 + 1, rgb)
        fill_rect(x0, y1 - 1, x1, y1, rgb)
        fill_rect(x0, y0, x0 + 1, y1, rgb)
        fill_rect(x1 - 1, y0, x1, y1, rgb)

    def draw_text(x, y, text, rgb, scale=1):
        clean = _ascii_label(text, 160)
        sx = int(x)
        sy = int(y)
        adv = 6 * scale
        for ch in clean:
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for r_idx, row_bits in enumerate(glyph):
                for c_idx in range(5):
                    if (row_bits >> (4 - c_idx)) & 1:
                        px = sx + c_idx * scale
                        py = sy + r_idx * scale
                        fill_rect(px, py, px + scale, py + scale, rgb)
            sx += adv
        return sx

    def draw_diff_segments(x, y, diff_str, max_x):
        cx = int(x)
        for kind, seg_txt in _parse_inline_diff_segments(diff_str):
            if cx >= max_x - 18:
                break
            clean = _ascii_label(seg_txt, max(4, (max_x - cx) // 6))
            seg_w = len(clean) * 6
            if kind == "del" and clean:
                fill_rect(cx - 1, y - 2, min(max_x, cx + seg_w + 1), y + 9, (254, 226, 226))
                end_x = draw_text(cx, y, clean, (185, 28, 28), scale=1)
                fill_rect(cx, y + 3, min(max_x, end_x), y + 4, (185, 28, 28))
                cx = end_x + 2
            elif kind == "ins" and clean:
                fill_rect(cx - 1, y - 2, min(max_x, cx + seg_w + 1), y + 9, (220, 252, 231))
                end_x = draw_text(cx, y, clean, (22, 101, 52), scale=1)
                cx = end_x + 2
            elif clean:
                cx = draw_text(cx, y, clean, (15, 23, 42), scale=1)

    # Header banner
    fill_rect(0, 0, w, 78, (241, 245, 249))
    fill_rect(0, 78, w, 80, (203, 213, 225))
    draw_text(24, 16, f"Academic Polish Redline Preview - {summary_info.get('case_id', '')}", (15, 23, 42), scale=2)
    stats = (
        f"Mode:{summary_info.get('execution_mode')} | Style:{summary_info.get('target_style')} | "
        f"Score:{summary_info.get('overall_audit_score')}/100 | Edited:{summary_info.get('edited_sentences')}/{summary_info.get('total_sentences')} | "
        f"HIGH:{summary_info.get('high_count')} MED:{summary_info.get('medium_count')}"
    )
    draw_text(24, 52, stats, (51, 65, 85), scale=1)

    y = 98
    shown = 0
    for rec in records:
        if y > h - 90 or shown >= 7:
            break
        diff_str = rec.get("diff", {}).get("inline_diff", rec.get("polished_text", ""))
        tags = ",".join(rec.get("edit_types") or [])
        rsn = rec.get("reason", "")
        fill_rect(24, y, w - 24, y + 78, (248, 250, 252) if rec.get("diff", {}).get("is_changed") else (255, 255, 255))
        stroke_rect(24, y, w - 24, y + 78, (226, 232, 240))
        draw_text(34, y + 10, f"{rec.get('sentence_id')} [{tags}]", (30, 64, 175), scale=1)
        draw_text(34, y + 29, "DIFF:", (71, 85, 105), scale=1)
        draw_diff_segments(70, y + 29, diff_str, w - 34)
        draw_text(34, y + 50, f"REASON: {rsn[:145]}", (71, 85, 105), scale=1)
        y += 88
        shown += 1

    def _chunk(ctype, cdata):
        body = ctype + cdata
        return struct.pack("!I", len(cdata)) + body + struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)

    stride = w * 3
    raw_scan = bytearray()
    for ry in range(h):
        raw_scan.append(0)
        raw_scan.extend(buf[ry * stride:(ry + 1) * stride])
    ihdr = struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw_scan), level=6)) + _chunk(b"IEND", b"")
    with open(png_path, "wb") as f:
        f.write(png_bytes)
    return "tier4_stdlib_png"


def _find_cjk_ttf():
    candidates = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        fdir = os.path.join(windir, "Fonts")
        candidates.extend([os.path.join(fdir, fn) for fn in ("msyh.ttc", "msyh.ttf", "simsun.ttc", "simhei.ttf")])
    candidates.extend([
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for cp in candidates:
        if cp and os.path.isfile(cp):
            return cp
    return None


def _render_png_headless_browser(html_path, png_path):
    """Tier 2.5: Headless Chromium/Edge screenshot of redline_diff.html for full CJK/HTML fidelity."""
    if not os.path.isfile(html_path):
        return None
    browsers = []
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            browsers.append(found)
    for env_k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_k)
        if base:
            for rel in (
                os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join("Google", "Chrome", "Application", "chrome.exe"),
            ):
                cand = os.path.join(base, rel)
                if os.path.isfile(cand) and cand not in browsers:
                    browsers.append(cand)
    abs_html = os.path.abspath(html_path).replace("\\", "/")
    file_uri = f"file:///{abs_html.lstrip('/')}"
    for b_exe in browsers[:2]:
        try:
            subprocess.run(
                [b_exe, "--headless", "--disable-gpu", "--no-sandbox", f"--screenshot={os.path.abspath(png_path)}", "--window-size=1280,960", file_uri],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True,
            )
            if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
                return "tier2_5_headless_browser"
        except Exception:
            continue
    return None


def _render_png_pillow(summary_info, records, png_path):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    try:
        w, h = 1100, 760
        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        font_path = _find_cjk_ttf()
        font_hdr = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
        font_body = ImageFont.truetype(font_path, 13) if font_path else ImageFont.load_default()
        draw.rectangle([0, 0, w, 78], fill=(241, 245, 249))
        draw.text((24, 14), f"Academic Polish Redline Preview - {summary_info.get('case_id', '')}", fill=(15, 23, 42), font=font_hdr)
        draw.text(
            (24, 44),
            f"Mode:{summary_info.get('execution_mode')} | Style:{summary_info.get('target_style')} | Score:{summary_info.get('overall_audit_score')}/100 | Edited:{summary_info.get('edited_sentences')}/{summary_info.get('total_sentences')}",
            fill=(51, 65, 85), font=font_body,
        )
        y = 96
        for rec in records[:7]:
            draw.rectangle([24, y, w - 24, y + 82], fill=(248, 250, 252), outline=(203, 213, 225))
            draw.text((34, y + 8), f"{rec.get('sentence_id')} [{','.join(rec.get('edit_types') or [])}]", fill=(30, 64, 175), font=font_body)
            clean_diff = " ".join(seg for _, seg in _parse_inline_diff_segments(rec.get("diff", {}).get("inline_diff", "")))
            draw.text((34, y + 28), f"TEXT: {clean_diff[:120]}", fill=(15, 23, 42), font=font_body)
            draw.text((34, y + 48), f"REASON: {str(rec.get('reason', ''))[:120]}", fill=(71, 85, 105), font=font_body)
            y += 90
        img.save(png_path, format="PNG")
        if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
            return "tier3_pillow_rasterizer"
    except Exception:
        return None
    return None


def _render_previews(out_dir, docx_redline_path, html_path, summary_info, records, force_tier=None):
    """
    5-tier PDF/PNG preview rendering chain:
    Tier 1: soffice/libreoffice -> redline_preview.pdf -> pymupdf/pdftoppm PNG
    Tier 2: pymupdf typeset PDF -> redline_preview.pdf -> pymupdf PNG
    Tier 2.5: headless browser (Edge/Chrome) screenshot of redline_diff.html
    Tier 3: PIL/Pillow PNG (auto-detected CJK TrueType font)
    Tier 4: pure stdlib zlib+struct PNG (red/green parsed diff blocks)
    Returns (tier_name, preview_files_rel).
    """
    preview_files = []
    for fn in os.listdir(out_dir):
        if fn.startswith("redline_preview_page") and fn.endswith(".png"):
            try:
                os.remove(os.path.join(out_dir, fn))
            except Exception:
                pass
    pdf_path = os.path.join(out_dir, "redline_preview.pdf")
    png_p1 = os.path.join(out_dir, "redline_preview_page1.png")

    # Tier 1: LibreOffice + PyMuPDF
    if force_tier in (None, "tier1"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice and os.path.isfile(docx_redline_path):
            try:
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_redline_path],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
                )
                lo_pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_redline_path))[0] + ".pdf")
                if os.path.isfile(lo_pdf):
                    shutil.move(lo_pdf, pdf_path)
                import pymupdf  # dynamic import
                doc = pymupdf.open(pdf_path)
                for p_idx in range(min(3, len(doc))):
                    pix = doc[p_idx].get_pixmap(dpi=180, alpha=False)
                    out_png = os.path.join(out_dir, f"redline_preview_page{p_idx + 1}.png")
                    pix.save(out_png)
                    preview_files.append(os.path.basename(out_png))
                doc.close()
                if preview_files:
                    return "tier1_libreoffice_pymupdf", ["redline_preview.pdf"] + preview_files
            except Exception:
                pass

    # Tier 2: PyMuPDF direct typesetting
    if force_tier in (None, "tier2"):
        try:
            import pymupdf  # dynamic import
            doc = pymupdf.open()
            pw, ph = 595, 842
            margin = 40
            page = doc.new_page(width=pw, height=ph)
            has_cjk = any(re.search(r"[\u4e00-\u9fff]", (r.get("source_text", "") + r.get("polished_text", ""))) for r in records)
            fontname = "china-s" if has_cjk else "helv"

            def new_p():
                return doc.new_page(width=pw, height=ph)

            y = margin
            # Header box
            page.draw_rect(pymupdf.Rect(margin, y, pw - margin, y + 64), color=(0.8, 0.85, 0.9), fill=(0.95, 0.97, 0.99))
            page.insert_text((margin + 10, y + 22), f"Academic Language Polish - Redline Review ({summary_info.get('case_id', '')})", fontsize=12, fontname=fontname, color=(0.08, 0.14, 0.25))
            meta_line = (
                f"Mode: {summary_info.get('execution_mode')} | Style: {summary_info.get('target_style')} | "
                f"Score: {summary_info.get('overall_audit_score')}/100 | Edited: {summary_info.get('edited_sentences')}/{summary_info.get('total_sentences')} | "
                f"HIGH: {summary_info.get('high_count')}"
            )
            page.insert_text((margin + 10, y + 44), meta_line, fontsize=8.5, fontname=fontname, color=(0.25, 0.32, 0.42))
            y += 78

            # Dimension score bar
            dim_scores = summary_info.get("dimension_scores") or {}
            if dim_scores:
                dim_txt = " | ".join(f"{k.split('_')[0]}:{v}" for k, v in dim_scores.items())
                page.insert_text((margin, y), f"8-Dim Audit: {dim_txt}", fontsize=7.5, fontname=fontname, color=(0.2, 0.3, 0.45))
                y += 18

            # Sentence diff entries (up to 3 pages)
            for rec in records:
                if y > ph - 110:
                    if len(doc) >= 3:
                        break
                    page = new_p()
                    y = margin
                sid = rec.get("sentence_id", "")
                tags = ", ".join(rec.get("edit_types") or [])
                src_t = rec.get("source_text", "")
                pol_t = rec.get("polished_text", "")
                rsn = rec.get("reason", "")
                changed = rec.get("diff", {}).get("is_changed", False)

                box_h = 74 if changed else 46
                fill_c = (0.98, 0.99, 1.0) if changed else (0.99, 0.99, 0.99)
                page.draw_rect(pymupdf.Rect(margin, y, pw - margin, y + box_h), color=(0.86, 0.89, 0.93), fill=fill_c)
                page.insert_text((margin + 8, y + 14), f"{sid} ({rec.get('section_role', '')}) [{tags}]", fontsize=8.5, fontname=fontname, color=(0.12, 0.28, 0.65))
                if changed:
                    page.insert_textbox(pymupdf.Rect(margin + 8, y + 18, pw - margin - 8, y + 36), f"- {src_t[:140]}", fontsize=8, fontname=fontname, color=(0.72, 0.12, 0.12))
                    page.insert_textbox(pymupdf.Rect(margin + 8, y + 36, pw - margin - 8, y + 54), f"+ {pol_t[:140]}", fontsize=8, fontname=fontname, color=(0.08, 0.48, 0.22))
                    page.insert_textbox(pymupdf.Rect(margin + 8, y + 54, pw - margin - 8, y + 72), f"Reason: {rsn[:135]}", fontsize=7.5, fontname=fontname, color=(0.3, 0.35, 0.42))
                else:
                    page.insert_textbox(pymupdf.Rect(margin + 8, y + 20, pw - margin - 8, y + 42), f"= {src_t[:140]}", fontsize=8, fontname=fontname, color=(0.35, 0.4, 0.48))
                y += box_h + 8

            doc.save(pdf_path)
            for p_idx in range(min(3, len(doc))):
                pix = doc[p_idx].get_pixmap(dpi=160, alpha=False)
                out_png = os.path.join(out_dir, f"redline_preview_page{p_idx + 1}.png")
                pix.save(out_png)
                preview_files.append(os.path.basename(out_png))
            doc.close()
            if preview_files:
                return "tier2_pymupdf_typeset", ["redline_preview.pdf"] + preview_files
        except Exception:
            pass

    # Tier 2.5: Headless Browser HTML->PNG screenshot
    if force_tier in (None, "tier2_5"):
        tier25 = _render_png_headless_browser(html_path, png_p1)
        if tier25:
            return tier25, ["redline_preview_page1.png"]

    # Tier 3: Pillow
    if force_tier in (None, "tier3"):
        tier3 = _render_png_pillow(summary_info, records, png_p1)
        if tier3:
            return tier3, ["redline_preview_page1.png"]

    # Tier 4: Pure stdlib PNG
    tier4 = _render_png_stdlib(summary_info, records, png_p1)
    return tier4, ["redline_preview_page1.png"]


# ----------------------------------------------------------------------
# 2. Text / HTML / CSV Deliverable Renderers & Output Contract Adapter
# ----------------------------------------------------------------------

def _render_revision_ledger_md(out_path, manifest, report):
    summ = report["summary"]
    is_audit_only = (manifest.get("execution_mode") == "audit_only")
    header_cols = "| ID | 章节 | 原文 | 润色后 | 内联 Diff | 修改类型 | 修改理由 |"
    sep_cols = "|-|-|-|-|-|-|-|"
    if is_audit_only:
        header_cols = "| ID | 章节 | 原文 | 润色后 | 内联 Diff | 修改类型 | 修改理由 | Suggested Revision | Audit Notes |"
        sep_cols = "|-|-|-|-|-|-|-|-|-|"
    lines = [
        f"# 学术语言润色逐句修改台账（{manifest.get('case_id', '')}）",
        "",
        f"- **执行模式**：`{manifest.get('execution_mode')}` | **目标风格**：`{manifest.get('target_style')}` | **编辑强度**：`{manifest.get('edit_intensity')}`",
        f"- **总句数**：`{summ['total_sentences']}` | **已修改**：`{summ['edited_sentences']}` | **保留原句**：`{summ['unchanged_sentences']}` | **平均编辑率**：`{summ['mean_edit_ratio']}`",
        f"- **8 维审计得分**：`{summ['overall_audit_score']}/100`（`HIGH`: `{summ['high_count']}` [可修 `{summ.get('actionable_high_count', summ['high_count'])}` / 顾问 `{summ.get('advisory_high_count', 0)}`], `MEDIUM`: `{summ['medium_count']}`, `LOW`: `{summ['low_count']}`）",
        "",
        "## 1. 逐句原文—改文—内联红线—修改类型—修改理由",
        "",
        header_cols,
        sep_cols,
    ]
    def esc(t):
        return str(t or "").replace("|", "\\|").replace("\n", " ")

    for r in report["merged_records"]:
        tags = ", ".join(r.get("edit_types") or [])
        base_row = (
            f"| {r['sentence_id']} | {esc(r.get('section_role'))} | {esc(r['source_text'])} | "
            f"{esc(r['polished_text'])} | {esc(r['diff']['inline_diff'])} | `{esc(tags)}` | {esc(r.get('reason'))} |"
        )
        if is_audit_only:
            sug = esc(r.get("suggested_text") or "")
            anote = esc(r.get("audit_notes") or r.get("reason") or "")
            base_row = base_row + f" {sug} | {anote} |"
        lines.append(base_row)

    if report.get("all_issues"):
        lines.extend(["", "## 2. 确定性审计告警", ""])
        for iss in report["all_issues"]:
            lines.append(f"- **[{iss.get('severity')}|repairable={iss.get('repairable', True)}] `{iss.get('code')}`** ({iss.get('sentence_id')}): {iss.get('detail')}")

    if report.get("uncertainty_items"):
        lines.extend(["", "## 3. 人工确认与边界不确定性清单", ""])
        for u in report["uncertainty_items"]:
            lines.append(f"- **`{u.get('code')}`** ({u.get('sentence_id', u.get('source', 'case'))}): {u.get('detail')}")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _render_revision_ledger_csv(out_path, report):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["sentence_id", "section_role", "in_scope", "is_changed", "edit_ratio", "source_text", "polished_text", "inline_diff", "edit_types", "reason", "uncertainty", "suggested_text", "audit_notes"])
        for r in report["merged_records"]:
            writer.writerow([
                r["sentence_id"],
                r.get("section_role", ""),
                r.get("in_scope", True),
                r["diff"]["is_changed"],
                r["diff"]["edit_ratio"],
                r["source_text"],
                r["polished_text"],
                r["diff"]["inline_diff"],
                "; ".join(r.get("edit_types") or []),
                r.get("reason", ""),
                r.get("uncertainty", ""),
                r.get("suggested_text", ""),
                r.get("audit_notes") or (r.get("reason", "") if r.get("suggested_text") else ""),
            ])


def _render_redline_html(out_path, manifest, report):
    summ = report["summary"]
    is_audit_only = (manifest.get("execution_mode") == "audit_only") or any(
        r.get("suggested_text") or r.get("audit_notes") for r in report["merged_records"]
    )
    rows_html = []
    for r in report["merged_records"]:
        ops_html = []
        for op in r["diff"]["diff_ops"]:
            if op["op"] == "equal":
                ops_html.append(html.escape(op["pol"]))
            elif op["op"] == "delete":
                ops_html.append(f'<del class="del">{html.escape(op["src"])}</del>')
            elif op["op"] == "insert":
                ops_html.append(f'<ins class="ins">{html.escape(op["pol"])}</ins>')
            elif op["op"] == "replace":
                ops_html.append(f'<del class="del">{html.escape(op["src"])}</del><ins class="ins">{html.escape(op["pol"])}</ins>')
        tags_html = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in (r.get("edit_types") or []))
        diag_cell = ""
        if is_audit_only:
            sug_h = html.escape(r.get("suggested_text") or "")
            note_h = html.escape(r.get("audit_notes") or r.get("reason") or "")
            diag_cell = f'<td><div class="sug"><strong>Suggested:</strong> {sug_h}</div><div class="reason"><strong>Audit Notes:</strong> {note_h}</div></td>'
        rows_html.append(
            f"<tr><td><strong>{html.escape(r['sentence_id'])}</strong><br><small>{html.escape(r.get('section_role', ''))}</small></td>"
            f"<td class=\"diff-cell\">{''.join(ops_html)}</td>"
            f"<td>{tags_html}<div class=\"reason\">{html.escape(r.get('reason', ''))}</div></td>{diag_cell}</tr>"
        )

    dim_badges = "".join(
        f'<div class="badge"><span>{html.escape(k)}</span><strong>{v}</strong></div>'
        for k, v in (summ.get("dimension_scores") or {}).items()
    )
    diag_th = '<th style="width:300px">Suggested Revision &amp; Audit Notes</th>' if is_audit_only else ""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redline Diff - {html.escape(manifest.get('case_id', ''))}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; margin: 24px; color: #0f172a; background: #f8fafc; }}
  .dash {{ background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; }}
  .badges {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }}
  .badge {{ background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 12px; }}
  .badge strong {{ margin-left: 6px; color: #1d4ed8; }}
  table {{ width: 100%; border-collapse: collapse; background: #ffffff; border: 1px solid #cbd5e1; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 10px 12px; vertical-align: top; text-align: left; font-size: 14px; line-height: 1.55; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  del.del {{ background: #fee2e2; color: #991b1b; text-decoration: line-through; padding: 1px 2px; border-radius: 3px; }}
  ins.ins {{ background: #dcfce7; color: #166534; text-decoration: none; padding: 1px 2px; border-radius: 3px; font-weight: 500; }}
  .tag {{ display: inline-block; background: #e0e7ff; color: #3730a3; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 4px; margin-bottom: 4px; }}
  .reason {{ margin-top: 4px; font-size: 12px; color: #475569; }}
  .sug {{ font-size: 13px; color: #0f766e; margin-bottom: 4px; }}
</style>
</head>
<body>
  <div class="dash">
    <h2>Academic Language Polish - Redline Review ({html.escape(manifest.get('case_id', ''))})</h2>
    <div>Mode: <code>{html.escape(str(manifest.get('execution_mode')))}</code> | Style: <code>{html.escape(str(manifest.get('target_style')))}</code> |
      Overall Audit Score: <strong>{summ['overall_audit_score']}/100</strong> | Edited: <strong>{summ['edited_sentences']}/{summ['total_sentences']}</strong> |
      HIGH: <strong>{summ['high_count']}</strong> | MED: <strong>{summ['medium_count']}</strong></div>
    <div class="badges">{dim_badges}</div>
  </div>
  <table>
    <thead><tr><th style="width:90px">ID</th><th>Inline Redline Diff</th><th style="width:280px">Edit Types &amp; Rationale</th>{diag_th}</tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


def _adapt_output_contract(contract_tpl, payload_values):
    """
    Preserve original output_contract schema and existing non-empty values while
    intelligently filling semantic keys (including empty {} / [] / "" placeholders).
    """
    if not isinstance(contract_tpl, dict):
        return contract_tpl
    out = {}
    for k, v in contract_tpl.items():
        kl = k.lower()
        # 1. Semantic direct matching BEFORE recursing into empty {} placeholders
        if kl == "case_id":
            out[k] = v if (isinstance(v, str) and v.strip()) else payload_values.get("case_id", "")
        elif kl == "deliverables":
            if isinstance(v, list):
                out[k] = list(payload_values.get("deliverables", {}).keys())
            elif isinstance(v, str):
                out[k] = ", ".join(payload_values.get("deliverables", {}).keys()) if not v.strip() else v
            elif isinstance(v, dict) and len(v) > 0:
                out[k] = {**payload_values.get("deliverables", {}), **v}
            else:
                out[k] = payload_values.get("deliverables", {})
        elif kl in ("audit_summary", "audit_report"):
            if isinstance(v, str):
                out[k] = json.dumps(payload_values.get("audit_summary", {}), ensure_ascii=False) if not v.strip() else v
            elif isinstance(v, dict) and len(v) > 0:
                out[k] = _adapt_output_contract(v, payload_values)
            else:
                out[k] = payload_values.get("audit_summary", {})
        elif kl in ("human_confirmation_items", "needs_human_confirmation"):
            if isinstance(v, dict) and len(v) == 0:
                out[k] = {"items": payload_values.get("human_confirmation_items", [])}
            elif isinstance(v, str):
                out[k] = json.dumps(payload_values.get("human_confirmation_items", []), ensure_ascii=False) if not v.strip() else v
            else:
                out[k] = payload_values.get("human_confirmation_items", [])
        elif kl == "bilingual_abstract":
            ba = payload_values.get("bilingual_abstract") or {}
            if isinstance(v, dict):
                out[k] = ba if isinstance(ba, dict) else {"text": str(ba)}
            elif isinstance(v, list):
                out[k] = ba.get("pairs", []) if isinstance(ba, dict) else []
            else:
                if isinstance(ba, dict):
                    zh_s = ba.get("zh", "")
                    en_s = ba.get("en", "")
                    out[k] = f"ZH: {zh_s}\nEN: {en_s}".strip() if (zh_s or en_s) else ""
                else:
                    out[k] = str(ba)
        elif kl in payload_values and (not isinstance(v, dict) or len(v) == 0):
            if isinstance(v, str) and v.strip():
                out[k] = v
            elif isinstance(v, list) and len(v) > 0:
                out[k] = v
            else:
                out[k] = payload_values[kl]
        elif isinstance(v, dict):
            out[k] = _adapt_output_contract(v, payload_values)
        elif "polished" in kl and "text" in kl and isinstance(v, (str, type(None))):
            out[k] = payload_values["polished_text"]
        elif ("edit_log" in kl or "revision" in kl or "changes" in kl) and isinstance(v, list):
            out[k] = payload_values["edit_log"]
        elif "uncertain" in kl and isinstance(v, list):
            out[k] = payload_values["uncertainty"]
        elif "source" in kl and isinstance(v, list):
            out[k] = payload_values["sources"]
        elif "status" in kl and isinstance(v, (str, type(None))):
            out[k] = payload_values["status"]
        else:
            out[k] = v
    return out


def compute_honest_status(manifest, report, allow_partial=False):
    summ = report["summary"]
    anomalies = manifest.get("anomalies", [])
    if summ["total_sentences"] == 0 or any(a["code"] == "NO_EDITABLE_SENTENCES" for a in anomalies):
        return "blocked"
    incomplete = any(i["code"] == "COVERAGE_INCOMPLETE" for i in report["all_issues"])
    if incomplete or allow_partial:
        return "partial"
    if summ["high_count"] > 0 or len(report.get("glossary_summary", {}).get("conflicts", [])) > 0:
        return "needs_review"
    if any(u.get("requires_human_confirmation") for u in report.get("uncertainty_items", [])):
        return "needs_review"
    return "ok"


def render_all_deliverables(manifest, blocks, report, out_dir, allow_partial=False, force_preview_tier=None):
    """
    Write full verifiable deliverable suite + result.json + 4-tier PNG/PDF preview to out_dir.
    """
    os.makedirs(out_dir, exist_ok=True)
    case_data = manifest.get("case_data") or {}
    case_id = manifest.get("case_id", "unknown")

    sent_map = {r["sentence_id"]: r["polished_text"] for r in report["merged_records"]}
    polished_full = case_ingestor.reconstruct_document(blocks, sent_map)

    # 1. Polished manuscript matching source format
    src_fmt = manifest.get("source_format", "markdown")
    ext = ".tex" if src_fmt == "latex" else (".txt" if src_fmt == "txt" else ".md")
    manuscript_name = f"polished_manuscript{ext}"
    manuscript_path = os.path.join(out_dir, manuscript_name)
    with open(manuscript_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(polished_full if polished_full.endswith("\n") else polished_full + "\n")

    # 2. Markdown & CSV ledgers + HTML redline + audit JSON
    ledger_md_path = os.path.join(out_dir, "revision_ledger.md")
    ledger_csv_path = os.path.join(out_dir, "revision_ledger.csv")
    redline_html_path = os.path.join(out_dir, "redline_diff.html")
    audit_json_path = os.path.join(out_dir, "audit_report.json")

    _render_revision_ledger_md(ledger_md_path, manifest, report)
    _render_revision_ledger_csv(ledger_csv_path, report)
    _render_redline_html(redline_html_path, manifest, report)
    with open(audit_json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: v for k, v in report.items() if k != "merged_records"}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 3. Clean & Track Changes DOCX
    clean_docx_path = os.path.join(out_dir, "polished_clean.docx")
    redline_docx_path = os.path.join(out_dir, "polished_redline.docx")
    title = case_data.get("title") or manifest.get("source_path") or case_id
    docx_meta = {
        "target_style": manifest.get("target_style"),
        "edit_intensity": manifest.get("edit_intensity"),
    }
    docx_redline.generate_clean_docx(clean_docx_path, str(title), blocks, sent_map, metadata=docx_meta)
    docx_redline.generate_redline_docx(redline_docx_path, str(title), blocks, report["merged_records"], audit_summary=report["summary"], metadata=docx_meta)

    # 4. 4-tier visual PDF/PNG preview
    summary_info = {
        "case_id": case_id,
        "execution_mode": manifest.get("execution_mode"),
        "target_style": manifest.get("target_style"),
        **report["summary"],
    }
    preview_tier, preview_files = _render_previews(out_dir, redline_docx_path, redline_html_path, summary_info, report["merged_records"], force_tier=force_preview_tier)

    has_cjk_any = any(re.search(r"[\u4e00-\u9fff]", (r.get("source_text", "") + r.get("polished_text", ""))) for r in report["merged_records"])
    preview_render_note = ""
    if preview_tier == "tier4_stdlib_png" and has_cjk_any:
        preview_render_note = "纯 stdlib 兜底图不含 CJK 矢量字形，请同时审阅同目录 redline_diff.html 与 polished_redline.docx。"

    # 5. Assemble result.json preserving case_id and output_contract
    status = compute_honest_status(manifest, report, allow_partial=allow_partial)
    edit_log = []
    for r in report["merged_records"]:
        entry = {
            "sentence_id": r["sentence_id"],
            "section": r.get("section_role", "body"),
            "source_text": r["source_text"],
            "polished_text": r["polished_text"],
            "is_changed": r["diff"]["is_changed"],
            "edit_ratio": r["diff"]["edit_ratio"],
            "inline_diff": r["diff"]["inline_diff"],
            "edit_types": r.get("edit_types", []),
            "reason": r.get("reason", ""),
            "needs_human_review": bool(r.get("uncertainty") or "needs_human_review" in (r.get("edit_types") or [])),
        }
        if r.get("bilingual_counterpart"):
            entry["bilingual_counterpart"] = r["bilingual_counterpart"]
        if r.get("suggested_text"):
            entry["suggested_text"] = r["suggested_text"]
        if r.get("audit_notes"):
            entry["audit_notes"] = r["audit_notes"]
        edit_log.append(entry)

    deliverable_names = [
        manuscript_name,
        "revision_ledger.md",
        "revision_ledger.csv",
        "redline_diff.html",
        "polished_clean.docx",
        "polished_redline.docx",
        "audit_report.json",
    ] + preview_files

    deliverables_meta = {}
    for dname in deliverable_names:
        dp = os.path.join(out_dir, dname)
        if os.path.isfile(dp):
            deliverables_meta[dname] = {"path": dname, "bytes": os.path.getsize(dp)}

    sources_list = []
    if manifest.get("source_path"):
        sources_list.append({"path": manifest["source_path"], "format": src_fmt, "sha256": manifest.get("normalized_source_sha256")})
    for g in manifest.get("glossary_files_detected", []):
        sources_list.append({"path": g, "format": "glossary"})

    # Deduplicated human confirmation items
    seen_hc = set()
    human_confirmation_items = []
    for u in report.get("uncertainty_items", []):
        key = (u.get("code", ""), u.get("sentence_id", u.get("source", "")), str(u.get("detail", ""))[:80])
        if key not in seen_hc:
            seen_hc.add(key)
            human_confirmation_items.append(u)
    for r in report["merged_records"]:
        if r.get("uncertainty") or "needs_human_review" in (r.get("edit_types") or []):
            det = r.get("uncertainty") or r.get("reason") or "Flagged needs_human_review."
            key = ("NEEDS_HUMAN_REVIEW", r["sentence_id"], det[:80])
            if key not in seen_hc:
                seen_hc.add(key)
                human_confirmation_items.append({
                    "source": "sentence_record",
                    "sentence_id": r["sentence_id"],
                    "code": "NEEDS_HUMAN_REVIEW",
                    "detail": det,
                    "requires_human_confirmation": True,
                })

    # Bilingual abstract assembly
    bilingual_abstract = {"zh": "", "en": "", "pairs": []}
    if manifest.get("execution_mode") == "bilingual_polish":
        abs_recs = [r for r in report["merged_records"] if r.get("section_role") == "abstract" and r.get("block_type") != "heading"]
        if not abs_recs:
            abs_recs = [r for r in report["merged_records"] if r.get("in_scope") and (r.get("bilingual_counterpart") or r.get("polished_text"))][:6]
        zh_parts, en_parts, pairs = [], [], []
        for r in abs_recs:
            pt = (r.get("polished_text") or "").strip()
            cp = (r.get("bilingual_counterpart") or "").strip()
            if not pt and not cp:
                continue
            pt_is_zh = bool(re.search(r"[\u4e00-\u9fff]", pt))
            cp_is_zh = bool(re.search(r"[\u4e00-\u9fff]", cp))
            zh_line = pt if pt_is_zh else (cp if cp_is_zh else "")
            en_line = cp if (cp and not cp_is_zh) else (pt if not pt_is_zh else "")
            if zh_line:
                zh_parts.append(zh_line)
            if en_line:
                en_parts.append(en_line)
            if zh_line or en_line:
                pairs.append({"sentence_id": r["sentence_id"], "zh": zh_line, "en": en_line})
        bilingual_abstract = {"zh": " ".join(zh_parts), "en": " ".join(en_parts), "pairs": pairs}

    audit_summary = {
        "status": status,
        "overall_audit_score": report["summary"]["overall_audit_score"],
        "high_count": report["summary"]["high_count"],
        "actionable_high_count": report["summary"].get("actionable_high_count", report["summary"]["high_count"]),
        "advisory_high_count": report["summary"].get("advisory_high_count", 0),
        "medium_count": report["summary"]["medium_count"],
        "low_count": report["summary"]["low_count"],
        "edited_sentences": report["summary"]["edited_sentences"],
        "total_sentences": report["summary"]["total_sentences"],
        "dimension_scores": report["summary"]["dimension_scores"],
    }

    core_payload = {
        "case_id": case_id,
        "status": status,
        "execution_mode": manifest.get("execution_mode"),
        "target_style": manifest.get("target_style"),
        "edit_intensity": manifest.get("edit_intensity"),
        "polished_text": polished_full,
        "edit_log": edit_log,
        "audit_summary": audit_summary,
        "audit_report": {
            "summary": report["summary"],
            "top_high_issues": report["top_high_issues"],
            "actionable_top_high_issues": report.get("actionable_top_high_issues", []),
            "non_repairable_advisories": report.get("non_repairable_advisories", []),
        },
        "glossary_conflicts": report.get("glossary_summary", {}).get("conflicts", []),
        "uncertainty": report.get("uncertainty_items", []),
        "human_confirmation_items": human_confirmation_items,
        "bilingual_abstract": bilingual_abstract,
        "sources": sources_list,
        "preview_render_tier": preview_tier,
        "deliverables": deliverables_meta,
    }
    if preview_render_note:
        core_payload["preview_render_note"] = preview_render_note

    result_obj = dict(core_payload)
    if "output_contract" in case_data:
        result_obj["output_contract"] = _adapt_output_contract(case_data["output_contract"], core_payload)

    result_path = os.path.join(out_dir, "result.json")
    with open(result_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return result_obj
