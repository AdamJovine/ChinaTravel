#!/usr/bin/env python3
"""Merge re-run plans into an existing zip, replacing matching UIDs.

Usage:
    python merge_rerun.py <base.zip> <rerun_dir> <output.zip>

Copies all plans from base.zip, replacing any whose UID appears in rerun_dir.
"""
import json
import os
import sys
import zipfile
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        print("Usage: merge_rerun.py <base.zip> <rerun_dir> <output.zip>")
        sys.exit(1)

    base_zip, rerun_dir, out_zip = sys.argv[1], sys.argv[2], sys.argv[3]

    # Load re-run plans (newest run subdir in rerun_dir)
    rerun_path = Path(rerun_dir)
    # Find the run directory (timestamped subdirs)
    run_dirs = sorted([d for d in rerun_path.iterdir() if d.is_dir()], reverse=True)
    if not run_dirs:
        print(f"No run directories found in {rerun_dir}")
        sys.exit(1)

    # Use the most recent run
    run_dir = run_dirs[0]
    print(f"Using run dir: {run_dir}")

    rerun_plans = {}
    for f in run_dir.glob("*.json"):
        if f.name == "run_info.json" or f.name.endswith("_debug.json"):
            continue
        uid = f.stem
        with open(f, encoding="utf-8") as fh:
            rerun_plans[uid] = json.load(fh)

    print(f"Re-run plans loaded: {len(rerun_plans)}")

    replaced = 0
    kept = 0
    with zipfile.ZipFile(base_zip) as zin, zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            data = zin.read(name)
            if name.endswith(".json") and not name.endswith("_debug.json"):
                uid = Path(name).stem
                if uid in rerun_plans:
                    data = json.dumps(rerun_plans[uid], ensure_ascii=False).encode("utf-8")
                    replaced += 1
                else:
                    kept += 1
            zout.writestr(name, data)

    print(f"Replaced {replaced} plans, kept {kept}")
    print(f"Output: {out_zip}")


if __name__ == "__main__":
    main()
