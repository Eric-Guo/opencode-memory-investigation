#!/usr/bin/env python3
"""Read-only macOS process memory samples. Never signals the target."""
import argparse
import datetime
import json
import platform
import re
import subprocess
import time
from pathlib import Path


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def mib(text, label):
    match = re.search(re.escape(label) + r"\s*([\d.]+)([KMGT])", text)
    return float(match[1]) * {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1048576}[match[2]] if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("--out", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("This helper uses macOS vmmap; use platform-native accounting elsewhere")
    if args.pid < 1 or args.samples < 1 or not 0 <= args.interval <= 60:
        parser.error("PID and sample count must be positive; interval must be 0..60 seconds")
    args.out = args.out.resolve()
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = []
    for index in range(args.samples):
        if index:
            time.sleep(args.interval)
        ps = command(["ps", "-p", str(args.pid), "-o", "pid,ppid,lstart,etime,%cpu,rss,vsz,command"])
        if ps["returncode"]:
            raise RuntimeError(f"PID {args.pid} is unavailable; partial evidence: {args.out}")
        vm = command(["vmmap", "-summary", str(args.pid)])
        path = args.out / f"sample-{index:02d}-vmmap.txt"
        path.write_text(vm["stdout"] + vm["stderr"])
        record = {
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "pid": args.pid,
            "ps": ps["stdout"].strip(),
            "physical_footprint_mib": mib(vm["stdout"], "Physical footprint:"),
            "peak_footprint_mib": mib(vm["stdout"], "Physical footprint (peak):"),
            "vmmap_returncode": vm["returncode"],
            "vmmap_file": str(path),
        }
        records.append(record)
        (args.out / "samples.json").write_text(json.dumps(records, indent=2) + "\n")
        print(json.dumps(record), flush=True)
    inventory = command(["lsof", "-nP", "-p", str(args.pid)])
    selected = [line for line in inventory["stdout"].splitlines() if any(
        marker in line for marker in ["COMMAND", " cwd ", " txt ", " IPv4 ", " IPv6 ", ".db", ".sqlite", ".log"]
    )]
    (args.out / "open-files.txt").write_text("\n".join(selected) + "\n" + inventory["stderr"])


if __name__ == "__main__":
    main()
