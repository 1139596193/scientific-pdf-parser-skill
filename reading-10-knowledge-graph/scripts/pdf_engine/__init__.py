#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_engine - Robust Scientific Document Parsing, Diagnostics & Layout Reconstruction
Zero-crash design: top-level imports are standard libraries only.
"""

from .pdf_diagnose import diagnose_pdf, probe_environment
from .layout_parser import parse_layout
from .table_formula_ocr import process_elements

__all__ = [
    "diagnose_pdf",
    "probe_environment",
    "parse_layout",
    "process_elements",
]
