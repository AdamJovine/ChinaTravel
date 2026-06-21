#!/usr/bin/env python3
"""
show_failures.py  <run_dir>  [--label LABEL]  [--epr]  [--uid UID]

Prints failing queries from a run directory so you can inspect them.

Options:
  --label LABEL   Only show failures with this constraint label
                  (e.g. total_budget, poi_timing, required_restaurant, other)
  --epr           Show EPR/commonsense failures instead of hard-constraint failures
  --uid UID       Show full detail for one specific query UID
  --limit N       Cap output at N queries (default 50)

Examples:
  python3 show_failures.py results/run_20260621_103959_cpsat_groq
  python3 show_failures.py results/run_20260621_103959_cpsat_groq --label total_budget
  python3 show_failures.py results/run_20260621_103959_cpsat_groq --label other
  python3 show_failures.py results/run_20260621_103959_cpsat_groq --uid 20250322130502929756
"""

import argparse
import json
import pathlib
import sys
import collections

Q_DIR = pathlib.Path(__file__).parent / "TPC_IJCAI_2026_phase1_EN"


def load_query_nl(uid: str) -> str:
    f = Q_DIR / f"{uid}.json"
    if f.exists():
        return json.loads(f.read_text()).get("nature_language", "")[:180]
    return "(query file not found)"


def show_detail(uid: str, result_dir: pathlib.Path):
    f = result_dir / f"{uid}.json"
    if not f.exists():
        print(f"  [not found: {uid}]")
        return
    d = json.loads(f.read_text())

    nl = load_query_nl(uid)
    print(f"\n{'─'*80}")
    print(f"UID : {uid}")
    print(f"NL  : {nl}")
    print(f"Hard pass : {d.get('hard_constraint_pass')}")
    print(f"CPsat budget : {d.get('cpsat_e_budget')}")

    for diag in d.get("hard_constraint_diagnostics", []):
        status = "✓" if diag.get("passed") else "✗"
        label = diag.get("label", "?")
        computed = diag.get("computed", {})
        error = diag.get("error")
        snippet = diag.get("full_snippet", diag.get("snippet", ""))[:300]
        if not diag.get("passed"):
            print(f"\n  {status} [{label}]")
            if computed:
                for k, v in computed.items():
                    print(f"      {k} = {v}")
            if error:
                print(f"      ERROR: {error}")
            print(f"      snippet: {snippet[:200]}")

    # Show itinerary summary
    print("\n  Itinerary:")
    for day in d.get("itinerary", []):
        acts = day.get("activities", [])
        for act in acts:
            t = act.get("type", "")
            pos = act.get("position", act.get("end", ""))
            cost = act.get("cost", 0)
            st = act.get("start_time", "")
            et = act.get("end_time", "")
            print(f"    Day {day['day']} {st}-{et}  {t:20s}  {pos[:45]:45s}  ¥{cost:.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--label", default=None, help="Filter by constraint label")
    ap.add_argument("--epr", action="store_true", help="Show EPR failures")
    ap.add_argument("--uid", default=None, help="Show full detail for one UID")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    result_dir = pathlib.Path(args.run_dir)
    if not result_dir.exists():
        # try relative to results/
        result_dir = pathlib.Path(__file__).parent / "results" / args.run_dir
    if not result_dir.exists():
        sys.exit(f"Run dir not found: {args.run_dir}")

    # Single-UID detail mode
    if args.uid:
        show_detail(args.uid, result_dir)
        return

    # Collect all failures
    hard_fail: list[dict] = []
    epr_fail:  list[dict] = []
    label_counter = collections.Counter()

    for f in sorted(result_dir.glob("*.json")):
        if any(x in f.name for x in ("debug", "run_info", "scores")):
            continue
        d = json.loads(f.read_text())
        uid = f.stem

        if not d.get("hard_constraint_pass", True):
            failing_diags = [
                diag for diag in d.get("hard_constraint_diagnostics", [])
                if not diag.get("passed", True)
            ]
            for diag in failing_diags:
                label_counter[diag.get("label", "unknown")] += 1
            hard_fail.append({"uid": uid, "diags": failing_diags, "data": d})

        # EPR: no separate field in these result files — detect via commonsense check
        # (The run_tpc evaluator writes this inline; we infer from diagnostics absence.)

    if args.epr:
        print("EPR failure inspection not available from result files alone.")
        print("Run:  python3 eval_submission.py <run_dir> --failures")
        return

    # Label summary
    print(f"\nRun: {result_dir.name}")
    print(f"Hard failures: {len(hard_fail)} / 1000\n")
    print("Constraint label counts:")
    for label, cnt in label_counter.most_common():
        marker = " ◄" if args.label and label == args.label else ""
        print(f"  {cnt:4d}  {label}{marker}")
    print()

    # Filter
    if args.label:
        filtered = [
            item for item in hard_fail
            if any(d.get("label") == args.label for d in item["diags"])
        ]
        print(f"Showing {min(len(filtered), args.limit)} of {len(filtered)} failures with label '{args.label}':\n")
    else:
        filtered = hard_fail
        print(f"Showing {min(len(filtered), args.limit)} of {len(filtered)} hard failures:\n")

    for item in filtered[: args.limit]:
        uid = item["uid"]
        nl = load_query_nl(uid)
        failing = [d for d in item["diags"] if not d.get("passed")]
        labels = ", ".join(d.get("label", "?") for d in failing)

        print(f"{'─'*72}")
        print(f"UID    : {uid}")
        print(f"NL     : {nl}")
        print(f"FAILED : {labels}")
        for diag in failing:
            if args.label and diag.get("label") != args.label:
                continue
            computed = diag.get("computed", {})
            snippet  = diag.get("full_snippet", diag.get("snippet", ""))
            if computed:
                print(f"  computed: {computed}")
            # Show first meaningful constraint line
            for line in snippet.splitlines():
                ls = line.strip()
                if ls and ls not in ("result=False", "result=True"):
                    print(f"  snippet:  {ls[:100]}")
                    break
        print()

    if len(filtered) > args.limit:
        print(f"... {len(filtered) - args.limit} more. Use --limit N to see more.")


if __name__ == "__main__":
    main()
