#!/usr/bin/env python3
"""Run queries not yet completed by either of the two ongoing runs.

Forward run  (runs ascending):  run_20260623_003631_cpsat_groq_7slotNIGHT
Reverse run  (runs descending): run_20260623_102233_cpsat_groq_7slotNIGHT_REV

Scans both dirs at startup for current completed files, then runs everything
in between (neither run has touched it yet).
"""
import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

QUERY_DIR = "/Users/adamjovine/Documents/ChinaTravel/chinatravel/data/en/tpc_phase1_en"
FWD_DIR   = "/Users/adamjovine/Documents/ChinaTravel/results/run_20260623_003631_cpsat_groq_7slotNIGHT"
REV_DIR   = "/Users/adamjovine/Documents/ChinaTravel/results/run_20260623_102233_cpsat_groq_7slotNIGHT_REV"
LISTEN_DIR = Path(__file__).parent / "listen"

METADATA = {"analysis_cache.json", "failure_by_category.json", "run_info.json", "scores.csv"}


def get_done(run_dir: str) -> set[str]:
    return {
        os.path.basename(f).replace(".json", "")
        for f in glob.glob(os.path.join(run_dir, "*.json"))
        if os.path.basename(f) not in METADATA
        and not os.path.basename(f).endswith("_debug.json")
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=2,
                    help="Parallel worker processes (default: 2)")
    ap.add_argument("--tag", default="7slotNIGHT_MID",
                    help="Run folder tag (default: 7slotNIGHT_MID)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the command and UID count without running")
    args = ap.parse_args()

    all_uids = sorted(
        os.path.basename(f).replace(".json", "")
        for f in glob.glob(os.path.join(QUERY_DIR, "*.json"))
    )

    done = get_done(FWD_DIR) | get_done(REV_DIR)
    remaining = [uid for uid in all_uids if uid not in done]

    print(f"Total queries : {len(all_uids)}")
    print(f"Done (fwd)    : {len(get_done(FWD_DIR))}")
    print(f"Done (rev)    : {len(get_done(REV_DIR))}")
    print(f"Done (combined): {len(done)}")
    print(f"Remaining     : {len(remaining)}")

    if not remaining:
        print("Nothing left to run!")
        return

    cmd = [
        sys.executable, "chinatravel_tpc/run_tpc.py",
        "--algo", "cpsat",
        "--api-model", "groq",
        "--query-dir", QUERY_DIR,
        "--tag", args.tag,
        "--bnb-nodes", "60",
        "--bnb-branching", "2",
        "--bnb-transport", "20",
        "--bnb-hotels", "20",
        "--bnb-restaurants", "20",
        "--bnb-batches", "10",
        "--cpsat-time-limit", "300",
        "--nl2sl-model", "claude-opus-4-8",
        "--workers", str(args.workers),
        "--uids", ",".join(remaining),
    ]

    if args.dry_run:
        print("\n[dry-run] command:")
        # Truncate --uids for readability
        display = cmd[:-1] + [f"<{len(remaining)} UIDs>"]
        print(" ".join(display))
        return

    subprocess.run(cmd, cwd=str(LISTEN_DIR), check=True)


if __name__ == "__main__":
    main()
