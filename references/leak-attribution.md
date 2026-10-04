# Move from a memory report to an owner

## Choose the missing evidence

| Evidence | Next useful experiment | What it can establish |
|---|---|---|
| One large idle footprint, no growth | Repeat inexpensive samples; inspect actual workload and loaded locations | Whether growth is happening now; not historical allocation ownership |
| Footprint grows during repeated snapshots | Repeat the same workload with `--snapshots none`, or use `final` | Workload growth without snapshot interference |
| Location lifecycle plateaus | Exercise the workload that differs: session/event churn, model streaming/cancellation, tools, or a specific plugin | Whether the plateau generalizes to that workload |
| Snapshot node bytes grow after warm-up | Compare types first, then constructors and A/B/C survivors | Separate application objects from code/shape metadata; find candidate owners |
| Object counts are stable but physical footprint grows | Compare unprofiled vmmap regions and runtime/native diagnostics | Narrow V8 capacity, allocator retention, external buffers, SQLite, native addons; heap totals alone cannot decide |
| Only redacted heap summaries remain | Reproduce in isolation with `--keep-snapshots` | Retaining edges and stable object IDs cannot be recovered from aggregate summaries |

Use equivalent work, cleanup, idle delay, binary hash, runtime, configuration, and measurement mode when comparing runs. Document different concurrency or machine memory pressure. Repeat only when an unresolved trend warrants it; do not silently turn a short reproduction into a soak test. A slope over a few checkpoints is evidence of growth, not a leak verdict. No universal MiB threshold separates a healthy process from a leak.

For growth tied to long sessions or periodic callbacks, use [soak-test design](soak-testing.md) to define elapsed-time coverage, completion inventories, and a bounded run before extending the probe.

## Unprofiled versus heap experiments

`isolated-probe.py` requires Python 3.11+ and an executable with the inspected OpenCode contracts. Snapshot modes additionally require a verified SIGUSR1 handler and Node for offline analysis. Each invocation creates a new private directory and terminates its own server. It records the executable SHA-256, workload, snapshot mode, settle delay, per-sample phase, completion status, and child exit code.

```bash
# A footprint control: no signal or heap snapshot at any checkpoint.
python3 <skill-dir>/scripts/isolated-probe.py \
  --binary /absolute/path/to/opencode --out /tmp/memory-control-<unique> \
  --workload session-events --snapshots none --checkpoints 100,200,300

# A separate run for object attribution. Preserve raw edges and IDs.
python3 <skill-dir>/scripts/isolated-probe.py \
  --binary /absolute/path/to/opencode --out /tmp/memory-heaps-<unique> \
  --workload session-events --snapshots each --checkpoints 100,200,300 --keep-snapshots
```

The session workload tests admission/deletion and a consuming SSE subscriber that disconnects. It does **not** test prompts, provider streams, tools, cancellation, slow consumers, WebSockets, or user plugins. Build those reproductions only after inspecting their lifecycle and side effects. Prefer local deterministic provider/tool fixtures over real billable requests or copied user credentials. A native-memory investigation may need a different workload entirely.

An optional `--snapshots final` run preserves all pre-snapshot physical samples and measures the final heap once. It helps check whether repeated snapshots changed the heap outcome. Checkpoint physical memory is sampled *before* its snapshot. Never subtract snapshot-accounted bytes from physical footprint and call the remainder a measured native allocation total: their accounting boundaries differ.

## A/B/C survivors and candidate retaining paths

Take A after warm-up and cleanup. Perform one workload batch and cleanup, take B, then another batch and cleanup, take C. Use three snapshots from **one uninterrupted process and V8 isolate**, in chronological order. Do not match IDs across restarted processes, separate workers, or different binaries. `--same-isolate` is an explicit assertion by the investigator, not automatic verification.

Obtain snapshot paths from that run's `results.json`, then run:

```bash
node <skill-dir>/scripts/trace-heap.mjs \
  /path/A.heapsnapshot /path/B.heapsnapshot /path/C.heapsnapshot \
  --same-isolate --out /tmp/heap-survivors-<unique>.json

# Narrow to an exact constructor label found in the summary.
node <skill-dir>/scripts/trace-heap.mjs \
  /path/A.heapsnapshot /path/B.heapsnapshot /path/C.heapsnapshot \
  --same-isolate --name Socket --limit 5 --out /tmp/socket-survivors-<unique>.json
```

The analyzer reports object/closure/array/native nodes absent in A, present in B, and still present in C, along with constructor totals and deltas for both intervals. It emits representative shortest root paths for the largest survivor groups, excluding edges explicitly marked weak. Sizes are shallow. Strings and source bodies are omitted; constructor and property names may still be sensitive, so the output is private and should not be published unchanged. Only the output filename and counts are printed to stdout.

This is a candidate-owner tool, not a full V8 garbage collector model: conditional ephemeron/internal edges, dominators, and retained sizes need verification in a heap viewer. A surviving object may be an intended module singleton, cache entry, a still-running timer, or measurement machinery. Snapshot creation and lazy compilation can replace runtime internals without growing application ownership. Cross-check type totals from `summarize-heap.mjs`; `trace-heap.mjs` deliberately does not trace code/hidden/shape nodes by default. A growing buffer attached to an object already present in A also needs ordinary size deltas and a heap viewer.

Tie a suspected leak to a completed lifecycle that should release the object, a retaining owner in current source, and repeated post-cleanup accumulation. Where practical, show that clearing/fixing that owner removes the growth in the isolated reproduction. Do not change cache policy solely to make a benchmark smaller.

## Native follow-up and reporting

If heap/application counts remain stable while unprofiled physical memory grows, retain the vmmap files and compare resident allocator/native regions. V8 committed capacity, native allocations and fragmentation can remain after the objects that caused them are gone. SQLite database/WAL sizes and cache limits do not report actual cache occupancy. Use allocator or SQLite counters when available; label an allocator explanation as a hypothesis until measured. Snapshot-native nodes cover only what V8 reports, not all native allocations.

Keep conclusions scoped to the tested workload and observation duration. Report material corrections to previous causal claims, not just new numbers. Preserve useful raw heaps while the retaining-path question is open, and confirm probe children have exited. A final live read-only sample and inventory can establish whether the original process remained undisturbed.

For changes to the analyzer, run `node --test scripts/test-trace-heap.mjs` from the skill directory. This uses real V8 snapshots to check an intentionally retained object, its path, subsequent release, private output permissions, and omission of string payloads.

Primary reference: [Node heap snapshot overhead and format](https://nodejs.org/api/v8.html#v8getheapsnapshotoptions). Snapshot collection blocks execution and requires substantial extra memory; the JSON schema can change with V8.
