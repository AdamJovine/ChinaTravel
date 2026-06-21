#!/usr/bin/env python3
"""Smart merge: keep whichever plan is better for each UID.

Priority: HC-pass > HC-fail. Among plans with equal HC status, pick higher DAV+DDR proxy.

Usage:
    python smart_merge.py <base_dir_or_zip> <rerun_dir_or_zip> <output_dir>

Both inputs can be directories of JSON plan files or .zip archives.
Output is a directory.
"""
import json
import sys
import zipfile
from pathlib import Path


def _plan_score(plan: dict) -> tuple[int, float]:
    """Return (hc_pass_int, dav_ddr_proxy) for comparison."""
    hc = 1 if plan.get("hard_constraint_pass", False) else 0
    activities = []
    for day in plan.get("itinerary", []):
        activities.extend(day.get("activities", []))
    n_attr = sum(1 for a in activities if a.get("type") == "attraction")
    n_meal = sum(1 for a in activities if a.get("type") in ("breakfast", "lunch", "dinner"))
    days = len([d for d in plan.get("itinerary", []) if d.get("day") not in (0, "0")])
    days = max(days, 1)
    dav = min(1.0, n_attr / (4.0 * days))
    ddr = min(1.0, n_meal / (3.0 * days))
    return (hc, dav + ddr)


def _load_plans(src: str) -> dict[str, dict]:
    p = Path(src)
    plans = {}
    if p.is_file() and src.endswith(".zip"):
        with zipfile.ZipFile(p) as zf:
            for name in zf.namelist():
                if name.endswith(".json") and not name.endswith("_debug.json") and "run_info" not in name:
                    data = zf.read(name)
                    plan = json.loads(data)
                    uid = plan.get("uid") or Path(name).stem
                    plans[uid] = plan
    elif p.is_dir():
        for f in p.rglob("*.json"):
            if f.name.endswith("_debug.json") or f.name == "run_info.json":
                continue
            plan = json.loads(f.read_text(encoding="utf-8"))
            uid = plan.get("uid") or f.stem
            plans[uid] = plan
    else:
        print(f"ERROR: {src} is neither a zip nor a directory")
        sys.exit(1)
    return plans


def main():
    if len(sys.argv) < 4:
        print("Usage: smart_merge.py <base> <rerun> <output_dir>")
        sys.exit(1)

    base_src, rerun_src, out_dir_str = sys.argv[1], sys.argv[2], sys.argv[3]

    print(f"Loading base: {base_src}")
    base_plans = _load_plans(base_src)
    print(f"  {len(base_plans)} plans")

    print(f"Loading rerun: {rerun_src}")
    rerun_plans = _load_plans(rerun_src)
    print(f"  {len(rerun_plans)} plans")

    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)

    kept_base = 0
    took_rerun = 0
    only_base = 0
    only_rerun = 0

    all_uids = set(base_plans) | set(rerun_plans)
    for uid in sorted(all_uids):
        if uid in base_plans and uid in rerun_plans:
            bs = _plan_score(base_plans[uid])
            rs = _plan_score(rerun_plans[uid])
            if rs >= bs:
                chosen = rerun_plans[uid]
                took_rerun += 1
            else:
                chosen = base_plans[uid]
                kept_base += 1
                if bs[0] != rs[0]:
                    print(f"  [keep-base] {uid}: base HC={bs[0]} DAV+DDR={bs[1]:.2f} > rerun HC={rs[0]} DAV+DDR={rs[1]:.2f}")
        elif uid in base_plans:
            chosen = base_plans[uid]
            only_base += 1
        else:
            chosen = rerun_plans[uid]
            only_rerun += 1

        (out_dir / f"{uid}.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nResult: {len(all_uids)} total plans in {out_dir}")
    print(f"  took rerun (better/equal): {took_rerun}")
    print(f"  kept base (better):        {kept_base}")
    print(f"  only in base:              {only_base}")
    print(f"  only in rerun:             {only_rerun}")


if __name__ == "__main__":
    main()
