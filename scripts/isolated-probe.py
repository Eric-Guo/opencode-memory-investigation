#!/usr/bin/env python3
"""Profile a disposable OpenCode server. Verify this build's SIGUSR1 handler first."""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


def checkpoint_list(value):
    try:
        values = [int(item) for item in value.split(",") if item]
    except ValueError:
        raise argparse.ArgumentTypeError("Use comma-separated positive cycle counts")
    if any(item < 1 for item in values) or values != sorted(set(values)):
        raise argparse.ArgumentTypeError("Checkpoints must be positive, unique and increasing")
    return values


def footprint(pid, destination):
    if not shutil.which("vmmap"):
        return None
    result = subprocess.run(["vmmap", "-summary", str(pid)], capture_output=True, text=True, timeout=30)
    destination.write_text(result.stdout + result.stderr)
    match = re.search(r"Physical footprint:\s*([\d.]+)([KMGT])", result.stdout)
    return float(match[1]) * {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1048576}[match[2]] if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New isolated evidence directory")
    parser.add_argument("--checkpoints", type=checkpoint_list, default=[], help="E.g. 10,30,100; omitted = fresh baseline only")
    parser.add_argument("--keep-snapshots", action="store_true")
    parser.add_argument("--snapshots", choices=["none", "final", "each"], default="each",
                        help="none: unprofiled control; final: snapshot only after last sample; each: heap series")
    parser.add_argument("--workload", choices=["location", "session-events"], default="location",
                        help="session-events also connects SSE, creates/deletes a session, drains events and disconnects")
    parser.add_argument("--settle", type=float, default=.5, help="Seconds to settle before each checkpoint (0..60)")
    parser.add_argument("--node", default="node", help="Node executable used for offline snapshot summarization")
    parser.add_argument("--timeout", type=float, default=60, help="Startup and per-snapshot deadline in seconds")
    args = parser.parse_args()
    binary = args.binary.resolve()
    node = shutil.which(args.node)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("--binary must be an existing executable")
    if args.timeout <= 0 or not 0 <= args.settle <= 60:
        parser.error("Requires a positive timeout and settle time of 0..60 seconds")
    if args.snapshots != "none" and (not node or not hasattr(signal, "SIGUSR1")):
        parser.error("Snapshot modes require Node and POSIX SIGUSR1")
    # Private directory + restrictive child umask keep snapshots and logs local.
    root = args.out.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ["home", "data", "cache", "config", "state", "project"]:
        (root / name).mkdir(mode=0o700)
    env = {key: os.environ[key] for key in ["PATH", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL"] if key in os.environ}
    env.update({
        "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state"),
        "OPENCODE_TEST_HOME": str(root / "home"), "OPENCODE_CONFIG_DIR": str(root / "config"),
        "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_CONFIG_PROJECT_DISABLE": "1",
        "OPENCODE_DISABLE_FFF": "1", "OPENCODE_FILEWATCHER_DISABLE": "1",
        "OPENCODE_PASSWORD": secrets.token_urlsafe(32),
    })
    with binary.open("rb") as executable:
        digest = hashlib.file_digest(executable, "sha256").hexdigest()
    report = {
        "binary": str(binary), "started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "binary_sha256": digest,
        "workload": args.workload, "snapshot_mode": args.snapshots, "settle_seconds": args.settle,
        "status": "running",
        "checkpoints": args.checkpoints, "samples": [], "child_exit_code": None,
    }
    def save():
        (root / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    log_path = root / "stdout.log"
    with log_path.open("w") as log:
        child = subprocess.Popen([str(binary), "serve", "--hostname", "127.0.0.1", "--port", "0"],
                                 env=env, cwd=root, stdout=log, stderr=log, umask=0o077)
        report["pid"] = child.pid
        save()
        try:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"Isolated server exited; inspect {log_path}")
                match = re.search(r"^server listening on (http://127\.0\.0\.1:\d+)\s*$", log_path.read_text(), re.M)
                if match:
                    url = match[1]
                    break
                time.sleep(.1)
            else:
                raise TimeoutError(f"No isolated loopback listener reported; inspect {log_path}")
            # Do not consult live registration, configuration or a system HTTP proxy.
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            auth = "Basic " + base64.b64encode(("opencode:" + env["OPENCODE_PASSWORD"]).encode()).decode()
            def request(route, method="GET", payload=None):
                req = urllib.request.Request(url + route, method=method,
                    data=None if payload is None else json.dumps(payload).encode(),
                    headers={"Authorization": auth, "Content-Type": "application/json"})
                with client.open(req, timeout=min(args.timeout, 30)) as response:
                    return response.read()
            def event(response):
                # A bounded wait for a complete SSE frame, not an unbounded response.read().
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    line = response.readline(1024 * 1024)
                    if not line:
                        raise RuntimeError("SSE closed before the expected event")
                    if line.startswith(b"data: "):
                        return json.loads(line[6:])
                raise TimeoutError("Expected SSE event was not observed")
            def sample(cycles):
                if json.loads(request("/api/debug/location")) != []:
                    raise RuntimeError("Isolated location inventory is not empty after eviction")
                if args.workload == "session-events":
                    if json.loads(request("/api/session"))["data"]:
                        raise RuntimeError("Isolated session inventory is not empty after deletion")
                    if json.loads(request("/api/session/active"))["data"]:
                        raise RuntimeError("Unexpected model execution in isolated session workload")
                profiled = any("snapshot_accounted_mib" in item for item in report["samples"])
                record = {
                    "cycles": cycles, "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "physical_footprint_mib": footprint(child.pid, root / f"cycles-{cycles}-vmmap.txt"),
                    "rss_mib": int(subprocess.check_output(["ps", "-p", str(child.pid), "-o", "rss="], text=True)) / 1024,
                    "physical_measurement_phase": "after earlier snapshots; profiler overhead included" if profiled else "pre-profiling",
                    "loaded_locations": 0,
                }
                report["samples"].append(record)
                save()
                final = args.checkpoints[-1] if args.checkpoints else 0
                if args.snapshots == "none" or (args.snapshots == "final" and cycles != final):
                    print(json.dumps(record), flush=True)
                    return
                directory = root / "data" / "opencode" / "log"
                pattern = f"heap-{child.pid}-*.heapsnapshot"
                before = set(directory.glob(pattern))
                if child.poll() is not None:
                    raise RuntimeError("Isolated child exited before snapshot")
                child.send_signal(signal.SIGUSR1)
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError("Isolated child exited during snapshot; verify this binary's signal handler")
                    files = set(directory.glob(pattern)) - before
                    logs = log_path.read_text(errors="replace")
                    for source in directory.glob("*.log"):
                        logs += source.read_text(errors="replace")
                    completed = [line for line in logs.splitlines() if "heap snapshot written" in line]
                    found = next((file for file in files if any(str(file) in line for line in completed)), None)
                    if found:
                        found.chmod(0o600)
                        break
                    time.sleep(.1)
                else:
                    raise TimeoutError("No completed snapshot log entry; partial artifacts preserved")
                summary_path = root / f"cycles-{cycles}-heap.json"
                summarized = subprocess.run([node, str(Path(__file__).with_name("summarize-heap.mjs")),
                                             str(found), "--out", str(summary_path)],
                                            capture_output=True, text=True, timeout=args.timeout)
                if summarized.returncode:
                    (root / f"cycles-{cycles}-analysis-error.txt").write_text(summarized.stderr)
                    raise RuntimeError("Snapshot summarization failed; raw snapshot and error preserved")
                summary = json.loads(summary_path.read_text())["summaries"][0]
                record.update({
                    "snapshot_accounted_mib": summary["snapshot_accounted_mib"], "nodes": summary["nodes"],
                    "serialized_snapshot_bytes": found.stat().st_size, "summary": str(summary_path),
                    "raw_snapshot_kept": args.keep_snapshots,
                })
                if args.keep_snapshots:
                    record["snapshot"] = str(found)
                else:
                    found.unlink()
                save()
                print(json.dumps(record), flush=True)
            time.sleep(args.settle)
            sample(0)
            query = "?" + urllib.parse.urlencode({"location[directory]": str(root / "project")})
            for cycles in range(1, (args.checkpoints[-1] if args.checkpoints else 0) + 1):
                request("/api/agent" + query)
                request("/api/model" + query)
                if args.workload == "session-events":
                    req = urllib.request.Request(url + "/api/event", headers={"Authorization": auth})
                    with client.open(req, timeout=min(args.timeout, 30)) as response:
                        if event(response)["type"] != "server.connected":
                            raise RuntimeError("Unexpected SSE connection contract")
                        session = json.loads(request("/api/session", "POST", {
                            "title": "isolated memory probe", "location": {"directory": str(root / "project")},
                        }))["data"]["id"]
                        request("/api/session/" + urllib.parse.quote(session, safe=""), "DELETE")
                        deadline = time.monotonic() + args.timeout
                        while time.monotonic() < deadline:
                            received = event(response)
                            if received["type"] == "session.deleted" and received["data"]["sessionID"] == session:
                                break
                        else:
                            raise TimeoutError("Session deletion was not delivered to SSE")
                request("/api/debug/location" + query, "DELETE")
                if cycles in args.checkpoints:
                    time.sleep(args.settle)
                    sample(cycles)
            report["status"] = "complete"
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            report["child_exit_code"] = child.returncode
            if report["status"] != "complete":
                report["status"] = "failed"
            save()
    print(json.dumps({"results": str(root / "results.json"), "child_stopped": child.poll() is not None}), flush=True)


if __name__ == "__main__":
    main()
