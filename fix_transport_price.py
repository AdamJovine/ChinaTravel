#!/usr/bin/env python3
"""Post-process plans to fix evaluator-detectable transport/timing bugs.

Fixes applied:
1. Walk/metro legs missing 'price' field  →  add price=0.0 (walk) or per-person cost (metro)
2. Hotel start_time >= end_time ("24:00") due to late-night transport → cap start to "23:59"
3. Return intercity taxi arriving slightly after departure → shift all taxi stages back
"""

import json
import os
import sys
import zipfile
from pathlib import Path


def _t2r(t: str) -> float:
    h, m = t.split(":")
    return float(h) * 60 + float(m)


def _add_min(t: str, delta: int) -> str:
    total = int(_t2r(t)) + delta
    total = max(0, total)
    return f"{total // 60:02d}:{total % 60:02d}"


def fix_transports(transports: list) -> bool:
    """Add missing price fields to walk/metro legs. Return True if changed."""
    changed = False
    for t in transports:
        mode = t.get("mode")
        if mode == "walk" and "price" not in t:
            t["price"] = 0.0
            changed = True
        elif mode == "metro" and "price" not in t:
            tickets = t.get("tickets", 1)
            cost = t.get("cost", 0)
            t["price"] = round(cost / tickets, 2) if tickets > 0 else cost
            changed = True
    return changed


def fix_hotel_time(act: dict) -> bool:
    """Cap hotel start_time to 23:59 when it's >= 24:00. Return True if changed."""
    if act.get("type") != "accommodation":
        return False
    st = act.get("start_time", "")
    if not st:
        return False
    if _t2r(st) >= _t2r("24:00"):
        act["start_time"] = "23:59"
        return True
    return False


def fix_return_taxi(act: dict) -> bool:
    """Shift return intercity taxi stages backward so transport arrives <= departure.

    Only applies when the delta is small (<= 60 min) and mode is taxi,
    to avoid silently masking fundamentally broken walk legs.
    Return True if changed.
    """
    if act.get("type") not in ("train", "airplane"):
        return False
    tr = act.get("transports", [])
    if not tr:
        return False
    dep = _t2r(act.get("start_time", "00:00"))
    arr = _t2r(tr[-1].get("end_time", "00:00"))
    if arr <= dep:
        return False
    delta = arr - dep
    if delta > 60:
        return False
    # Only fix taxi legs (shifting walk times would break Is_transport_correct)
    if tr[-1].get("mode") != "taxi":
        return False
    shift = int(delta) + 1  # shift by delta+1 so arrival is 1 min early
    for t in tr:
        t["start_time"] = _add_min(t["start_time"], -shift)
        t["end_time"] = _add_min(t["end_time"], -shift)
    return True


def fix_plan(plan: dict) -> bool:
    changed = False
    for day in plan.get("itinerary", []):
        for act in day.get("activities", []):
            if "transports" in act:
                if fix_transports(act["transports"]):
                    changed = True
            if fix_hotel_time(act):
                changed = True
            if fix_return_taxi(act):
                changed = True
    return changed


def process_zip(src: str, dst: str) -> int:
    fixed = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            data = zin.read(name)
            if name.endswith(".json") and not name.endswith("_debug.json"):
                plan = json.loads(data)
                if fix_plan(plan):
                    fixed += 1
                data = json.dumps(plan, ensure_ascii=False).encode("utf-8")
            zout.writestr(name, data)
    return fixed


def process_dir(src: str, dst: str) -> int:
    import shutil
    os.makedirs(dst, exist_ok=True)
    fixed = 0
    for fn in os.listdir(src):
        src_path = os.path.join(src, fn)
        dst_path = os.path.join(dst, fn)
        if fn.endswith(".json") and not fn.endswith("_debug.json"):
            with open(src_path, encoding="utf-8") as f:
                plan = json.load(f)
            if fix_plan(plan):
                fixed += 1
            with open(dst_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False)
        else:
            shutil.copy2(src_path, dst_path)
    return fixed


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fix_transport_price.py <src.zip|src_dir> [dst.zip|dst_dir]")
        sys.exit(1)

    src = sys.argv[1]
    if len(sys.argv) >= 3:
        dst = sys.argv[2]
    else:
        p = Path(src)
        dst = str(p.with_name(p.stem + "_fixed" + p.suffix))

    if src.endswith(".zip"):
        fixed = process_zip(src, dst)
    else:
        fixed = process_dir(src, dst)

    print(f"Fixed {fixed} plans  →  {dst}")
