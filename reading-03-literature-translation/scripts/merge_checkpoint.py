#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_checkpoint.py - Incremental Bilingual Translation Merger, Change Tracker & Early-Drop Checkpointer
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import glob
import argparse
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from render_deliverables import render_deliverables


def parse_agent_json_tolerant(fpath):
    """
    Fault-tolerant parser supporting raw JSON arrays, wrapper dicts,
    unit_id-keyed dicts, and Markdown ```json ... ``` fenced blocks.
    """
    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
        raw_text = f.read().strip()

    if not raw_text:
        return []

    # Strip Markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw_text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        raw_text = fence_match.group(1).strip()
    elif raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text).strip()

    # Try direct JSON parse, with trailing comma cleanup fallback
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([\]\}])", r"\1", raw_text)
        data = json.loads(cleaned)

    records = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("unit_id"):
                records.append(item)
    elif isinstance(data, dict):
        # Check wrapper keys
        list_payload = None
        for wrapper_k in ("translations", "repairs", "units", "items", "results", "data"):
            if wrapper_k in data and isinstance(data[wrapper_k], list):
                list_payload = data[wrapper_k]
                break
            elif wrapper_k in data and isinstance(data[wrapper_k], dict):
                data = data[wrapper_k]
                break

        if list_payload is not None:
            for item in list_payload:
                if isinstance(item, dict) and item.get("unit_id"):
                    records.append(item)
        else:
            # unit_id keyed dict
            for k, v in data.items():
                if k in ("case_id", "batch_id", "metadata", "status"):
                    continue
                if isinstance(v, str):
                    records.append({"unit_id": k, "translated_text": v})
                elif isinstance(v, dict):
                    rec = dict(v)
                    rec["unit_id"] = rec.get("unit_id") or k
                    records.append(rec)

    return records


def load_json_safe(path, default):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def excerpt(text, limit=80):
    s = str(text or "").replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit] + "..."


def diff_centered_excerpts(old_text, new_text, before_ctx=45, after_ctx=75):
    """
    Generate diff-centered excerpts anchored at the first differing character
    between old_text and new_text so reviewers immediately see the exact edit.
    """
    old_s = re.sub(r"\s+", " ", str(old_text or "")).strip()
    new_s = re.sub(r"\s+", " ", str(new_text or "")).strip()

    if old_s == new_s:
        return excerpt(old_s, before_ctx + after_ctx), excerpt(new_s, before_ctx + after_ctx)

    min_len = min(len(old_s), len(new_s))
    diff_pos = min_len
    for i in range(min_len):
        if old_s[i] != new_s[i]:
            diff_pos = i
            break

    start = max(0, diff_pos - before_ctx)
    end_old = min(len(old_s), diff_pos + after_ctx)
    end_new = min(len(new_s), diff_pos + after_ctx)

    b_snip = ("..." if start > 0 else "") + old_s[start:end_old] + ("..." if end_old < len(old_s) else "")
    a_snip = ("..." if start > 0 else "") + new_s[start:end_new] + ("..." if end_new < len(new_s) else "")
    return b_snip, a_snip


def merge_checkpoint(work_dir, output_dir, input_dir=None, add_batch=None, apply_repair=None, sync_dir=None):
    """Merge batch or repair outputs into merged_translations.json, track changes, and refresh output."""
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    source_units_data = load_json_safe(os.path.join(work_dir, "source_units.json"), {"units": []})
    units = source_units_data.get("units", [])
    valid_trans_ids = {
        u["unit_id"] for u in units
        if u.get("translate_policy") == "translate" and u.get("in_scope", True)
    }

    merged_path = os.path.join(work_dir, "merged_translations.json")
    merged_trans = load_json_safe(merged_path, {})

    changes_path = os.path.join(work_dir, "change_records.json")
    changes_data = load_json_safe(changes_path, {"records": []})
    change_records = changes_data.get("records", [])

    # Load repair queue to enrich trigger_codes automatically
    rq_data = load_json_safe(os.path.join(work_dir, "repair_queue.json"), {"items": []})
    rq_codes_map = {
        item["unit_id"]: [iss["code"] for iss in item.get("issues", [])]
        for item in rq_data.get("items", [])
    }

    files_to_process = []
    mode = "batch"
    if add_batch:
        files_to_process.append((add_batch, "batch"))
    if apply_repair:
        files_to_process.append((apply_repair, "repair"))
        mode = "repair"
    if sync_dir:
        for bf in sorted(glob.glob(os.path.join(sync_dir, "*.json"))):
            files_to_process.append((bf, "batch"))

    added_cnt = 0
    repaired_cnt = 0
    filtered_cnt = 0
    now_iso = datetime.datetime.now().isoformat()

    for fpath, fmode in files_to_process:
        if not os.path.exists(fpath):
            print(f"[merge_checkpoint] Warning: file not found: {fpath}")
            continue
        records = parse_agent_json_tolerant(fpath)

        for rec in records:
            uid = str(rec.get("unit_id", "")).strip()
            role = rec.get("role", "")

            # Filter out invalid unit_ids or context_tail leakages
            if role == "context_only_do_not_retranslate" or uid not in valid_trans_ids:
                filtered_cnt += 1
                change_records.append({
                    "timestamp": now_iso,
                    "unit_id": uid,
                    "action": "filtered_invalid_or_context",
                    "before_excerpt": "",
                    "after_excerpt": excerpt(rec.get("translated_text", "")),
                    "trigger_codes": [],
                    "reason": f"Ignored unit '{uid}' (not an active translatable unit or marked context_tail).",
                })
                continue

            new_tgt = str(rec.get("translated_text", "")).strip()
            new_rat = str(rec.get("rationale", "")).strip()
            new_unc = rec.get("uncertainty", "")

            if not new_tgt:
                continue

            if uid in merged_trans:
                old_tgt = merged_trans[uid].get("translated_text", "")
                if fmode == "batch":
                    # Avoid accidental overwrite by overlapping context tail in add-batch mode
                    # unless explicitly intended or previous was empty
                    if old_tgt and old_tgt != new_tgt:
                        filtered_cnt += 1
                        b_ex, a_ex = diff_centered_excerpts(old_tgt, new_tgt)
                        change_records.append({
                            "timestamp": now_iso,
                            "unit_id": uid,
                            "action": "skipped_duplicate_in_batch",
                            "before_excerpt": b_ex,
                            "after_excerpt": a_ex,
                            "trigger_codes": [],
                            "reason": f"Skipped duplicate unit '{uid}' in batch mode to protect existing translation (use --apply-repair to overwrite).",
                        })
                        continue
                else:
                    # Repair mode: overwrite and record audit diff
                    if old_tgt != new_tgt:
                        repaired_cnt += 1
                        trig = rec.get("trigger_codes") or rq_codes_map.get(uid, ["MANUAL_REPAIR"])
                        reason = rec.get("repair_reason") or rec.get("rationale") or f"Applied targeted repair from {os.path.basename(fpath)}"
                        b_ex, a_ex = diff_centered_excerpts(old_tgt, new_tgt)
                        change_records.append({
                            "timestamp": now_iso,
                            "unit_id": uid,
                            "action": "repair",
                            "before_excerpt": b_ex,
                            "after_excerpt": a_ex,
                            "trigger_codes": trig,
                            "reason": reason,
                        })
                    merged_trans[uid] = {
                        "unit_id": uid,
                        "translated_text": new_tgt,
                        "rationale": new_rat or merged_trans[uid].get("rationale", ""),
                        "uncertainty": new_unc,
                    }
                    continue

            # New unit insertion
            merged_trans[uid] = {
                "unit_id": uid,
                "translated_text": new_tgt,
                "rationale": new_rat,
                "uncertainty": new_unc,
            }
            added_cnt += 1

    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump(merged_trans, f, ensure_ascii=False, indent=2)

    with open(changes_path, "w", encoding="utf-8") as f:
        json.dump({"total_records": len(change_records), "records": change_records}, f, ensure_ascii=False, indent=2)

    # Early-drop safeguard: refresh output_dir/result.json immediately
    res = render_deliverables(work_dir, output_dir, input_dir=input_dir, is_final=False)

    print(f"[merge_checkpoint] Added={added_cnt}, Repaired={repaired_cnt}, Filtered={filtered_cnt} | Progress={res['progress']['completed_units']}/{res['progress']['total_translatable_units']} (status={res['status']})")
    return res


def main():
    parser = argparse.ArgumentParser(description="Incremental Translation Merger & Checkpointer")
    parser.add_argument("--work-dir", required=True, help="Work directory")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--input-dir", default=None, help="Input directory containing case.json")
    parser.add_argument("--add-batch", default=None, help="Path to a translated batch JSON output")
    parser.add_argument("--apply-repair", default=None, help="Path to a repaired unit JSON output")
    parser.add_argument("--sync-dir", default=None, help="Directory of translated batch JSON files to sync")
    args = parser.parse_args()

    merge_checkpoint(
        work_dir=args.work_dir,
        output_dir=args.output_dir,
        input_dir=args.input_dir,
        add_batch=args.add_batch,
        apply_repair=args.apply_repair,
        sync_dir=args.sync_dir,
    )


if __name__ == "__main__":
    main()
