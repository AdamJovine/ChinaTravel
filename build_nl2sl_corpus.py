#!/usr/bin/env python3
"""Build a NL2SL few-shot corpus from existing plan files.

Reads plan JSON files from one or more run directories (or zip files), extracts
the (nature_language, gt_programs) pairs, reverse-maps the GT DSL programs back
to approximate JSON params so they can be used as dynamic few-shot examples, and
writes a corpus JSON file usable by nl2sl_translate(corpus_path=...).

Usage:
    python build_nl2sl_corpus.py \\
        --runs results/FULL1000_BNB_24/run_20260618_211715_bnb_groq \\
        --output rank_cache/nl2sl_corpus.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

_LISTEN_ROOT = Path(__file__).resolve().parent.parent / "LISTEN-active"
if str(_LISTEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_LISTEN_ROOT))

from chinatravel_tpc.nl2sl import _gt_programs_to_json


def load_plans_from_dir(path: str) -> list[dict]:
    plans = []
    for fn in sorted(os.listdir(path)):
        if fn.endswith("_debug.json") or not fn.endswith(".json") or "run_info" in fn:
            continue
        with open(os.path.join(path, fn), encoding="utf-8") as f:
            plans.append(json.load(f))
    return plans


def load_plans_from_zip(path: str) -> list[dict]:
    plans = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith("_debug.json") or not name.endswith(".json"):
                continue
            plans.append(json.loads(zf.read(name)))
    return plans


def build_corpus(plan_sources: list[str]) -> list[dict]:
    """Build corpus entries from a list of run directories or zip files."""
    seen_nls: set[str] = set()
    corpus: list[dict] = []

    for src in plan_sources:
        if src.endswith(".zip"):
            plans = load_plans_from_zip(src)
        else:
            plans = load_plans_from_dir(src)

        for plan in plans:
            nl = plan.get("nl2sl_nature_language", "").strip()
            gt = plan.get("nl2sl_ground_truth", [])
            people = int(plan.get("people_number", 1))
            days = len(plan.get("itinerary", []))

            if not nl or not gt or nl in seen_nls:
                continue
            seen_nls.add(nl)

            # Reverse-map GT DSL programs → approximate JSON params
            params = _gt_programs_to_json(gt, people, days)
            # Serialize to compact JSON (same format as few-shot outputs)
            params_json = json.dumps(params, ensure_ascii=False, separators=(",", ":"))

            corpus.append({
                "nl": nl,
                "json": params_json,
                "city": plan.get("target_city", ""),
            })

    return corpus


def main():
    ap = argparse.ArgumentParser(description="Build NL2SL few-shot corpus from plan files")
    ap.add_argument("--runs", nargs="+", required=True,
                    help="One or more run directories or zip files containing plan JSONs")
    ap.add_argument("--output", default="rank_cache/nl2sl_corpus.json",
                    help="Output corpus JSON file (default: rank_cache/nl2sl_corpus.json)")
    args = ap.parse_args()

    print(f"Building corpus from {len(args.runs)} source(s)…")
    corpus = build_corpus(args.runs)
    print(f"  {len(corpus)} unique queries collected")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Corpus written to {out}")

    # Quick sanity check: show a few entries
    for entry in corpus[:3]:
        params = json.loads(entry["json"])
        non_null = {k: v for k, v in params.items()
                    if v not in (None, False, [], {})}
        print(f"\n  [{entry['city']}] {entry['nl'][:80]}…")
        print(f"    Extracted params: {non_null}")


if __name__ == "__main__":
    main()
