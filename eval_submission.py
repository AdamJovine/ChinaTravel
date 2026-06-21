#!/usr/bin/env python3
"""
eval_submission.py — Local replication of the TPC@IJCAI2026 competition evaluator.

Reads plans directly from a directory or zip of result JSONs (like LISTEN_v2.zip).
Reconstructs query_data from the plan files themselves (no HuggingFace dependency).
Runs all commonsense + hard-constraint + preference checks and prints the exact
weighted score that the leaderboard computes.

Usage:
    python eval_submission.py LISTEN_v2/           # directory of plan JSONs
    python eval_submission.py LISTEN_v2.zip        # zip of plan JSONs
    python eval_submission.py LISTEN_v3.zip --breakdown   # show per-check breakdown
    python eval_submission.py LISTEN_v3.zip --failures    # list failing UIDs per category
"""

import argparse
import json
import os
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import numpy as np
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

# ── add project root so chinatravel imports work ──────────────────────────────
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints
from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2
from chinatravel.symbol_verification.concept_func import func_dict


# ─────────────────────────────────────────────────────────────────────────────
# Preference score programs  (copied verbatim from eval_tpc.py)
# ─────────────────────────────────────────────────────────────────────────────

_PR_ATTRACTION = """
attraction_count = 0
for activity in allactivities(plan):
    if activity_type(activity) == 'attraction':
        attraction_count += 1
result = attraction_count / (4 * day_count(plan))
"""

_PR_TRANSPORT = """
time_cost = 0
transport_count = 0
for activity in allactivities(plan):
    transports = activity_transports(activity)
    if transports != []:
        transport_count += 1
        time_cost += innercity_transport_time(transports)
average_time_cost = time_cost / transport_count if transport_count > 0 else -1
result = (-1 / 105) * average_time_cost + 8 / 7
"""

_PR_DINING = """
res_count = 0
for activity in allactivities(plan):
    if activity_type(activity) in ['breakfast', 'lunch', 'dinner']:
        res_count += 1
res_count = res_count / (day_count(plan))
result = res_count / 3
"""

_DEFAULT_PR = [_PR_ATTRACTION, _PR_TRANSPORT, _PR_DINING]


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _eval_preference(plan_json: dict) -> list[float]:
    """Return [DAV, ATT, DDR] for one plan, each in [0,1]."""
    scores = []
    for code in _DEFAULT_PR:
        vd = deepcopy(func_dict)
        vd["plan"] = plan_json
        try:
            exec(code, {"__builtins__": {"set": set}}, vd)
            scores.append(_clamp(vd.get("result", 0.0)))
        except Exception:
            scores.append(0.0)
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Plan loading
# ─────────────────────────────────────────────────────────────────────────────

def _is_uid(stem: str) -> bool:
    return stem.isdigit() and len(stem) >= 18


def _load_plans_from_dir(path: str) -> dict[str, dict]:
    plans = {}
    for fn in os.listdir(path):
        if not fn.endswith(".json") or fn.endswith("_debug.json"):
            continue
        uid = fn[:-5]
        if not _is_uid(uid):
            continue
        with open(os.path.join(path, fn), encoding="utf-8") as f:
            plans[uid] = json.load(f)
    return plans


def _load_plans_from_zip(path: str) -> dict[str, dict]:
    plans = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json") or name.endswith("_debug.json"):
                continue
            uid = Path(name).stem
            if not _is_uid(uid):
                continue
            plans[uid] = json.loads(zf.read(name))
    return plans


def load_plans(path: str) -> dict[str, dict]:
    if path.endswith(".zip"):
        return _load_plans_from_zip(path)
    return _load_plans_from_dir(path)


# ─────────────────────────────────────────────────────────────────────────────
# Reconstruct query_data from plan files
# ─────────────────────────────────────────────────────────────────────────────

def build_query_data(plans: dict[str, dict]) -> dict[str, dict]:
    """
    The competition evaluator needs:
        query_data[uid] = {
            "uid":            str,
            "target_city":    str,
            "start_city":     str,
            "people_number":  int,
            "days":           int,
            "hard_logic_py":  list[str],   # oracle constraints
            "nature_language": str,
        }

    All of these are embedded in every plan file:
        - nl2sl_ground_truth  →  hard_logic_py
        - people_number, start_city, target_city  →  direct fields
        - days  →  len(itinerary)
    """
    query_data = {}
    for uid, plan in plans.items():
        hard_logic_py = plan.get("nl2sl_ground_truth", [])
        if not hard_logic_py:
            hard_logic_py = plan.get("hard_logic_py", [])
        query_data[uid] = {
            "uid":             uid,
            "target_city":     plan.get("target_city", ""),
            "start_city":      plan.get("start_city", ""),
            "people_number":   plan.get("people_number", 1),
            "days":            len(plan.get("itinerary", [])),
            "hard_logic_py":   hard_logic_py,
            "nature_language": plan.get("nl2sl_nature_language", ""),
        }
    return query_data


# ─────────────────────────────────────────────────────────────────────────────
# Main evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(submission_path: str, *, breakdown: bool = False, failures: bool = False) -> dict:
    print(f"\nLoading plans from: {submission_path}")
    plans = load_plans(submission_path)
    print(f"  {len(plans)} plan files loaded")

    query_data = build_query_data(plans)
    query_index = sorted(plans.keys())
    n = len(query_index)

    # ── Infer language from city names in the plans ───────────────────────────
    # Plans for the TPC phase-1 use English city names; let the evaluator
    # auto-detect rather than forcing "zh" (which would mismatch DB keys).
    lang = None   # None → _infer_lang() picks "en" or "zh" per query

    # ── 1. Commonsense / environment constraints ──────────────────────────────
    print("\n[1/3] Evaluating commonsense (environment) constraints …")
    macro_comm, micro_comm, comm_result_agg, comm_pass_id = evaluate_commonsense_constraints(
        query_index, query_data, plans, verbose=False, lang=lang
    )

    # ── 2. Hard / logical constraints ────────────────────────────────────────
    print("\n[2/3] Evaluating hard (logical) constraints …")
    macro_logi, micro_logi, c_macro_logi, c_micro_logi, logi_result_agg, logi_pass_id = (
        evaluate_hard_constraints_v2(
            query_index, query_data, plans,
            env_pass_id=comm_pass_id,
            verbose=False, lang=lang,
        )
    )

    # ── 3. FPR — all constraints must pass ───────────────────────────────────
    all_pass_id = set(comm_pass_id) & set(logi_pass_id)
    fpr = len(all_pass_id) / n * 100

    # ── 4. Preference metrics (DAV / ATT / DDR) — only over fully-passing plans
    print("\n[3/3] Evaluating preference metrics …")
    pref_scores: list[np.ndarray] = []
    for uid in tqdm(query_index):
        if uid not in all_pass_id:
            continue
        pref_scores.append(np.array(_eval_preference(plans[uid])))

    if pref_scores:
        mean_pref = np.mean(pref_scores, axis=0) * 100
    else:
        mean_pref = np.zeros(3)
    dav, att, ddr = mean_pref[0], mean_pref[1], mean_pref[2]

    # ── 5. Final weighted score ───────────────────────────────────────────────
    final_score = (
        0.10 * micro_comm
        + 0.10 * macro_comm
        + 0.25 * c_micro_logi
        + 0.40 * fpr
        + 0.05 * dav
        + 0.05 * att
        + 0.05 * ddr
    )

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("COMPETITION SCORE BREAKDOWN")
    print("=" * 60)
    print(f"  EPR-micro  (10%): {micro_comm:6.2f}%  →  contribution {0.10*micro_comm:5.2f}")
    print(f"  EPR-macro  (10%): {macro_comm:6.2f}%  →  contribution {0.10*macro_comm:5.2f}")
    print(f"  C-LPR      (25%): {c_micro_logi:6.2f}%  →  contribution {0.25*c_micro_logi:5.2f}")
    print(f"  FPR        (40%): {fpr:6.2f}%  →  contribution {0.40*fpr:5.2f}")
    print(f"  DAV         (5%): {dav:6.2f}%  →  contribution {0.05*dav:5.2f}")
    print(f"  ATT         (5%): {att:6.2f}%  →  contribution {0.05*att:5.2f}")
    print(f"  DDR         (5%): {ddr:6.2f}%  →  contribution {0.05*ddr:5.2f}")
    print("-" * 60)
    print(f"  OVERALL SCORE:    {final_score:6.2f}%")
    print("=" * 60)

    print(f"\n  Total plans:           {n}")
    print(f"  Commonsense pass:      {len(comm_pass_id)} / {n}  ({len(comm_pass_id)/n*100:.1f}%)")
    print(f"  Hard-constraint pass:  {len(logi_pass_id)} / {n}  ({len(logi_pass_id)/n*100:.1f}%)")
    print(f"  ALL pass (FPR):        {len(all_pass_id)} / {n}  ({len(all_pass_id)/n*100:.1f}%)")

    # ── Per-check breakdown ───────────────────────────────────────────────────
    if breakdown:
        print("\n" + "─" * 60)
        print("COMMONSENSE CHECK BREAKDOWN  (1 = failed)")
        print("─" * 60)
        comm_cols = [c for c in comm_result_agg.columns if c != "data_id"]
        col_totals = comm_result_agg[comm_cols].sum()
        for col in comm_cols:
            fail_n = int(col_totals[col])
            pct = fail_n / n * 100
            bar = "█" * int(pct / 2)
            print(f"  {fail_n:4d}/{n}  ({pct:5.1f}%)  {bar}  {col}")

    # ── Failing UIDs per category ─────────────────────────────────────────────
    if failures:
        comm_fail_ids = set(query_index) - set(comm_pass_id)
        logi_fail_ids = set(query_index) - set(logi_pass_id)

        print("\n" + "─" * 60)
        print(f"COMMONSENSE FAILURES: {len(comm_fail_ids)}")
        print("─" * 60)
        comm_cols = [c for c in comm_result_agg.columns if c != "data_id"]
        for col in comm_cols:
            uids = [
                query_index[i]
                for i, row in comm_result_agg[col].items()
                if row == 1
            ]
            if uids:
                print(f"\n  [{col}]  ({len(uids)} plans)")
                for u in uids[:8]:
                    print(f"    {u}")
                if len(uids) > 8:
                    print(f"    … and {len(uids)-8} more")

        print("\n" + "─" * 60)
        print(f"HARD-CONSTRAINT FAILURES: {len(logi_fail_ids)}")
        print("─" * 60)
        logi_cols = [c for c in logi_result_agg.columns if c != "data_id"]
        for col in logi_cols:
            fail_count = int((logi_result_agg[col] == 0).sum())
            if fail_count:
                print(f"  {col}: {fail_count} failures")

    results = {
        "EPR_micro": micro_comm,
        "EPR_macro": macro_comm,
        "C_LPR":     c_micro_logi,
        "FPR":       fpr,
        "DAV":       dav,
        "ATT":       att,
        "DDR":       ddr,
        "overall":   final_score,
        "n_total":   n,
        "n_comm_pass": len(comm_pass_id),
        "n_logi_pass": len(logi_pass_id),
        "n_all_pass":  len(all_pass_id),
        "comm_fail_ids": sorted(set(query_index) - set(comm_pass_id)),
        "logi_fail_ids": sorted(set(query_index) - set(logi_pass_id)),
        "all_fail_ids":  sorted(set(query_index) - all_pass_id),
    }
    return results


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate a TPC submission against the competition scoring formula")
    parser.add_argument("submission", help="Path to plan directory or .zip file")
    parser.add_argument("--breakdown", action="store_true",
                        help="Print per-check commonsense failure counts")
    parser.add_argument("--failures", action="store_true",
                        help="List failing UIDs per failure category")
    parser.add_argument("--json-out", metavar="FILE",
                        help="Write results dict to JSON file")
    args = parser.parse_args()

    results = evaluate(args.submission, breakdown=args.breakdown, failures=args.failures)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nResults written to {args.json_out}")


if __name__ == "__main__":
    main()
