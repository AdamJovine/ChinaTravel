#!/usr/bin/env python3
"""
Compare nl2sl extraction quality across different Groq models.

Usage:
    python test_nl2sl_models.py                   # test all candidates on failing + sample
    python test_nl2sl_models.py --failing-only     # only 21 failing queries from best run
    python test_nl2sl_models.py --all-failing      # also include failing_queries/ dir
    python test_nl2sl_models.py --models qwen/qwen3-32b meta-llama/llama-4-scout-17b-16e-instruct

Outputs a JSON summary to results/nl2sl_model_comparison/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

# ---- path setup ----
_HERE = Path(__file__).resolve().parent
_LISTEN_ROOT = _HERE.parent / "LISTEN-active"
if str(_LISTEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_LISTEN_ROOT))

QUERY_DIR  = _HERE / "TPC_IJCAI_2026_phase1_EN"
BEST_RUN   = _HERE / "results/FULL1000_BNB_18/run_20260618_013134_bnb_groq"
FAILING_Q  = _HERE / "failing_queries"
OUT_DIR    = _HERE / "results/nl2sl_model_comparison"

CANDIDATE_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "qwen/qwen3.6-27b",
    # "openai/gpt-oss-safeguard-20b",  # safety model — skip for generation tasks
]

# ---- helpers ----

def _load_query(uid: str) -> dict:
    return json.loads((QUERY_DIR / f"{uid}.json").read_text())


def _boilerplate_keywords() -> tuple[str, ...]:
    return ("day_count(plan)", "people_count(plan)", "activity_tickets", "taxi_cars")


def _optional_snippets(snippets: list[str]) -> list[str]:
    bp = _boilerplate_keywords()
    return [s for s in snippets if not any(k in s for k in bp)]


def _snippet_key(s: str) -> str:
    """Normalise whitespace for comparison."""
    import re
    return re.sub(r"\s+", " ", s.strip())


def _compare(pred: list[str], gt: list[str]) -> dict:
    pred_opt = [_snippet_key(s) for s in _optional_snippets(pred)]
    gt_opt   = [_snippet_key(s) for s in _optional_snippets(gt)]
    pred_set = set(pred_opt)
    gt_set   = set(gt_opt)
    missing  = list(gt_set - pred_set)
    extra    = list(pred_set - gt_set)
    exact    = pred_set == gt_set
    return {
        "exact_match": exact,
        "gt_optional_count": len(gt_opt),
        "pred_optional_count": len(pred_opt),
        "missing": missing,
        "extra": extra,
    }


def _make_nl2sl_client(model: str):
    from groq_client import FreeLLMPreferenceClient
    print(f"  [client] model={model}")
    return FreeLLMPreferenceClient(model_name=model)


def _run_nl2sl(queries: list[dict], model: str, cache_dir: Path) -> list[dict]:
    from chinatravel_tpc.nl2sl import nl2sl_translate
    client = _make_nl2sl_client(model)
    # Model-specific cache subdir so each model's responses are cached separately
    safe_model = model.replace("/", "_").replace(":", "_")
    model_cache_dir = cache_dir / safe_model
    results = []
    for i, q in enumerate(queries):
        uid = q["uid"]
        nl  = q.get("nature_language", "")
        people = int(q["people_number"])
        days   = int(q["days"])
        gt_snippets = q.get("hard_logic_py", [])

        t0 = time.time()
        try:
            pred = nl2sl_translate(
                nl, people, days,
                llm_client=client,
                cache_dir=model_cache_dir,
            )
            elapsed = time.time() - t0
            cmp = _compare(pred, gt_snippets)
            results.append({
                "uid": uid,
                "nl": nl[:200],
                "model": model,
                "elapsed_s": round(elapsed, 2),
                "predicted": pred,
                "ground_truth": gt_snippets,
                **cmp,
                "error": None,
            })
            status = "OK" if cmp["exact_match"] else f"DIFF(missing={len(cmp['missing'])},extra={len(cmp['extra'])})"
        except Exception as e:
            elapsed = time.time() - t0
            results.append({
                "uid": uid, "nl": nl[:200], "model": model, "elapsed_s": round(elapsed, 2),
                "predicted": [], "ground_truth": gt_snippets,
                "exact_match": False, "gt_optional_count": len(_optional_snippets(gt_snippets)),
                "pred_optional_count": 0, "missing": [], "extra": [], "error": str(e),
            })
            status = f"ERROR({e})"
        print(f"  [{i+1}/{len(queries)}] {uid[:16]} → {status}")

    return results


def _print_summary(model: str, results: list[dict]) -> dict:
    total    = len(results)
    exact    = sum(1 for r in results if r["exact_match"])
    errors   = sum(1 for r in results if r["error"])
    diffs    = total - exact - errors
    print(f"\n{'='*60}")
    print(f"Model: {model}")
    print(f"  Exact match (pred==GT): {exact}/{total}  ({100*exact/total:.1f}%)")
    print(f"  Diffs:  {diffs}  |  Errors: {errors}")
    for r in results:
        if not r["exact_match"]:
            tag = "ERR " if r["error"] else "DIFF"
            print(f"  {tag} {r['uid'][:16]}  missing={len(r['missing'])} extra={len(r['extra'])}", end="")
            if r["missing"]:
                print(f"  MISSING: {r['missing'][0][:60]}", end="")
            if r["error"]:
                print(f"  {r['error'][:60]}", end="")
            print()
    return {"model": model, "total": total, "exact": exact, "diffs": diffs, "errors": errors}


# ---- main ----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=CANDIDATE_MODELS)
    ap.add_argument("--failing-only", action="store_true",
                    help="Only test the 21 failing queries from the best run")
    ap.add_argument("--all-failing", action="store_true",
                    help="Also include all queries in failing_queries/ dir")
    ap.add_argument("--sample-size", type=int, default=30,
                    help="Number of passing queries to sample (default 30)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = _HERE / "cache" / "nl2sl_model_test"

    # ---- collect test queries ----
    with open(BEST_RUN / "run_info.json") as f:
        run_info = json.load(f)

    failed_uids = [q["uid"] for q in run_info["queries"] if not q.get("passed")]
    passed_uids = [q["uid"] for q in run_info["queries"] if q.get("passed")]

    test_uids: list[str] = list(failed_uids)  # always include the 21 failures

    if args.all_failing:
        fq_uids = [f.stem for f in sorted(FAILING_Q.glob("*.json"))]
        new = [u for u in fq_uids if u not in set(test_uids)]
        test_uids.extend(new)
        print(f"Added {len(new)} queries from failing_queries/ dir")

    if not args.failing_only:
        random.seed(args.seed)
        sample = random.sample(passed_uids, min(args.sample_size, len(passed_uids)))
        test_uids.extend(sample)
        print(f"Added {len(sample)} random passing queries")

    # Load query objects
    queries = []
    for uid in test_uids:
        p = QUERY_DIR / f"{uid}.json"
        if p.exists():
            queries.append(json.loads(p.read_text()))
        else:
            print(f"WARNING: query file not found for {uid}")

    print(f"\nTotal test queries: {len(queries)}")
    print(f"  - From best-run failures: {len(failed_uids)}")
    if not args.failing_only:
        print(f"  - Random passing sample: {args.sample_size}")
    if args.all_failing:
        extra = len(queries) - len(failed_uids) - (0 if args.failing_only else args.sample_size)
        print(f"  - From failing_queries/: {max(0, extra)}")
    print(f"\nModels to test: {args.models}")

    # ---- run each model ----
    all_results: dict[str, list[dict]] = {}
    summaries: list[dict] = []

    for model in args.models:
        print(f"\n{'='*60}")
        print(f"Testing: {model}")
        print(f"{'='*60}")
        try:
            results = _run_nl2sl(queries, model, cache_dir)
        except Exception as e:
            print(f"FATAL ERROR for {model}: {e}")
            continue
        all_results[model] = results
        summary = _print_summary(model, results)
        summaries.append(summary)

    # ---- overall comparison table ----
    if summaries:
        print(f"\n{'='*60}")
        print("SUMMARY TABLE")
        print(f"{'='*60}")
        print(f"{'Model':<50} {'Exact%':>8} {'Exact':>6} {'Total':>6}")
        print("-" * 72)
        for s in sorted(summaries, key=lambda x: -x["exact"]):
            pct = 100 * s["exact"] / s["total"] if s["total"] else 0
            print(f"{s['model']:<50} {pct:>7.1f}% {s['exact']:>6} {s['total']:>6}")

    # ---- split by failing vs passing ----
    if len(summaries) > 0:
        failing_set = set(failed_uids)
        for label, uid_filter in [("Failing queries (should be hard)", failing_set),
                                   ("Passing queries (regression check)", set(test_uids) - failing_set)]:
            if not uid_filter:
                continue
            print(f"\n--- {label} (n={len(uid_filter)}) ---")
            print(f"{'Model':<50} {'Exact%':>8} {'Exact':>6} {'Total':>6}")
            for model in args.models:
                if model not in all_results:
                    continue
                sub = [r for r in all_results[model] if r["uid"] in uid_filter]
                exact = sum(1 for r in sub if r["exact_match"])
                total = len(sub)
                pct   = 100 * exact / total if total else 0
                print(f"{model:<50} {pct:>7.1f}% {exact:>6} {total:>6}")

    # ---- cross-model agreement: where do models disagree? ----
    if len(all_results) >= 2:
        models_present = [m for m in args.models if m in all_results]
        print(f"\n--- Cross-model disagreement (where models produce different predictions) ---")
        disagree_uids = []
        for uid in test_uids:
            preds = {}
            for m in models_present:
                for r in all_results[m]:
                    if r["uid"] == uid:
                        preds[m] = tuple(sorted(_snippet_key(s) for s in _optional_snippets(r.get("predicted", []))))
                        break
            if len(set(preds.values())) > 1:
                disagree_uids.append(uid)
        print(f"  Queries where models disagree: {len(disagree_uids)}/{len(test_uids)}")
        for uid in disagree_uids[:10]:
            is_failing = uid in set(failed_uids)
            tag = "[FAIL]" if is_failing else "[PASS]"
            print(f"  {tag} {uid[:16]}")

    # ---- load existing best-run nl2sl predictions for comparison ----
    print(f"\n--- Comparison to best-run (openai/gpt-oss-120b) predictions ---")
    baseline_preds: dict[str, list[str]] = {}
    for uid in test_uids:
        result_path = BEST_RUN / f"{uid}.json"
        if result_path.exists():
            try:
                r = json.loads(result_path.read_text())
                baseline_preds[uid] = r.get("nl2sl_predicted", [])
            except Exception:
                pass

    if baseline_preds:
        for model in args.models:
            if model not in all_results:
                continue
            agree = 0
            total = 0
            for r in all_results[model]:
                uid = r["uid"]
                if uid not in baseline_preds:
                    continue
                total += 1
                bp = tuple(sorted(_snippet_key(s) for s in _optional_snippets(baseline_preds[uid])))
                mp = tuple(sorted(_snippet_key(s) for s in _optional_snippets(r.get("predicted", []))))
                if bp == mp:
                    agree += 1
            pct = 100 * agree / total if total else 0
            safe = model.replace("/", "_")
            print(f"  {model:<50} agrees with baseline: {pct:.1f}% ({agree}/{total})")

    # ---- write output ----
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = OUT_DIR / f"comparison_{ts}.json"
    out_data = {
        "timestamp": ts,
        "models": args.models,
        "n_queries": len(queries),
        "failed_uids": failed_uids,
        "summaries": summaries,
        "results": all_results,
    }
    out_file.write_text(json.dumps(out_data, indent=2, ensure_ascii=False))
    print(f"\nResults written to {out_file}")


if __name__ == "__main__":
    main()
