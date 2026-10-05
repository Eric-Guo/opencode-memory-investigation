---
name: opencode-memory
description: Investigate OpenCode server memory usage and suspected leaks, including Node SEA processes on macOS and Linux. Measure unprofiled growth, reproduce location and session/subscription lifecycles in isolation, and analyze V8 heap survivors and candidate retaining paths.
---

# OpenCode memory investigation

Distinguish startup/dependency costs, cached project state, native allocations, and workload-dependent leaks. Establish a pre-profiling baseline before taking a heap snapshot. Use only the authorized investigation scope; do not start a background daemon or model workload merely to look for a leak. Use the current PID, binary, build, and source; past measurements are examples, not thresholds.

## Start with inexpensive evidence

1. Resolve the requested process with `ps`; inspect its exact command, uptime, CPU and executable. Record repository revision and working-tree status. Check process state: a zombie is not live RSS, and kill(pid, 0) succeeding does not establish liveness. In cloud runs, verify the sampler and target share the relevant PID namespace; an invisible foreign PID is not proof of exit. A filename such as `opencode2-v2.0.7` may contain a different dev build; corroborate with service registration or that process's startup log.
2. On macOS or Linux, collect several samples with the helper below. It only reads process state and writes a new private evidence directory. It never signals or restarts the process.

   ```bash
   python3 <skill-dir>/scripts/sample-process.py <pid> --out /tmp/opencode-memory-live-<unique>
   ```

   Defaults: three samples, ten seconds apart. Use `--samples 1` for a quick inventory. Linux uses `/proc/<pid>/status` and `smaps_rollup` with FD/thread counts. Its RSS/PSS and anonymous/file-backed categories are not macOS physical footprint. See [Linux accounting and budgets](references/linux.md).
3. For a workspace-sharing owner, use task/session queries through its pure file client and sample the actual owner only from its owning execution context. Do not open another server on its database or read provider credentials for status. Read `run-opencode-helper` when that runtime mode needs clarification.
4. Read the relevant server diagnostics and source. Use [the OpenCode reference](references/opencode.md) for file anchors and authenticated location inventory. Keep service passwords in memory and out of tool output. Do not invoke a command that ensures or replaces the managed server merely to obtain status.
5. Compare like metrics. macOS physical footprint, RSS, virtual reservation, V8 heap, external buffers, and serialized snapshot size are different measures. A SQLite cache limit is not its actual occupancy. A single large number does not establish growth or a leak.

## Obtain and summarize a heap only when useful

Prefer an existing snapshot or an isolated reproduction before capturing the live process. A snapshot pauses execution and can substantially increase both peak and subsequent memory usage. Explain that concrete effect before live capture; respect existing authorization without adding a blanket confirmation requirement. Do not restart the live service just to reset the measurement.

Verify the target runtime and installed signal handler first. In the investigated OpenCode build, `packages/cli/src/heap.ts` handles SIGUSR1 by calling `writeHeapSnapshot`; SIGUSR1 is **not** a reliable way to open the inspector in this application. Do not assume that behavior for a different binary or a Bun runtime.

When live capture is appropriate, send the verified signal once, wait for that PID's `heap snapshot written` log entry, verify the resulting JSON is complete, and make the file private. Do not infer completion from a briefly stable file size. Keep the snapshot local; it can contain session data and credentials. Label all subsequent process measurements as affected by profiling.

```bash
node <skill-dir>/scripts/summarize-heap.mjs /path/before.heapsnapshot /path/after.heapsnapshot --out /tmp/heap-summary-<unique>.json
```

The summary includes node self-size totals, type and constructor counts, loaded script source bytes, duplicate package roots, plugin reload generations, and pairwise deltas. It omits string values and source bodies. Its totals include snapshot-reported native nodes; call them **snapshot-accounted memory**, not `process.memoryUsage().heapUsed`. Top constructor bytes are shallow sizes, not retained sizes. For survivor cohorts and candidate retaining paths, use `trace-heap.mjs` as described in [leak attribution](references/leak-attribution.md); use a heap viewer for dominators and precise retained sizes. Keep raw snapshots when further attribution is needed: summaries cannot reconstruct edges or object identities.

## Reproduce a lifecycle leak in isolation

Read [the OpenCode reference](references/opencode.md) before running this helper against an unfamiliar build. Verify the current location/agent/model endpoint contracts, and its signal handler when requesting snapshots. Start with an unprofiled control when investigating physical memory growth:

```bash
python3 <skill-dir>/scripts/isolated-probe.py \
  --binary /absolute/path/to/the/installed/opencode \
  --out /tmp/opencode-memory-probe-<unique> \
  --snapshots none \
  --checkpoints 10,30,100
```

Without `--checkpoints`, it measures only a fresh-server baseline. It starts its own server on a loopback ephemeral port with temporary HOME/TMP/data/configuration/cache/state/project directories, no inherited provider credentials, model fetching disabled, and filesystem watchers disabled. It does not accept a live server URL or use `serve --service`. It waits for agent/plugin initialization, reads models, evicts its own project, and verifies the loaded-location list is empty before each sample. It terminates only its own child, including on failure.

- The child has a default 600-second total deadline and 2048-MiB sampled RSS ceiling. Set `--max-seconds` and `--max-rss-mib` for the available machine budget; RSS polling is not a hard allocation limit and cannot guarantee OOM prevention.
- `--snapshots none` never signals the child; all footprint samples are unprofiled. Node is not needed for analysis in this mode.
- `--snapshots final` measures the whole run unprofiled and takes one snapshot after the final physical sample.
- `--snapshots each` (the backward-compatible default) takes a heap at every checkpoint, including cycle zero. Only the first physical sample is unprofiled.
- `--workload session-events` additionally connects SSE, creates and deletes one session without prompting a model, observes the deletion event, then disconnects. Checkpoints also verify zero sessions and executions. Inspect that build's session/event endpoint contracts first.
- `--keep-snapshots` preserves raw snapshots for attribution; otherwise successful summaries replace them. Failures retain partial evidence. `--settle` sets the delay before checkpoint sampling (default 0.5 seconds).

Compare after warm-up rather than treating initialization as leaked memory. If live graphs accumulate but isolated ones do not, investigate the differing plugins, client subscriptions, active sessions, and workload. For plugin attribution or model/tool leaks, adapt the relevant workload rather than endlessly repeating this lifecycle test. Isolation settings do not prove that every built-in dependency makes zero network requests.

When a report stops at “no leak confirmed,” read [leak attribution](references/leak-attribution.md) to choose the next experiment. Do not attribute an entire footprint increase to heap snapshots without a comparable unprofiled control, or declare native memory healthy because JavaScript constructors are flat.

## Extend to a bounded soak test when needed

For long-session growth, timer-driven behavior, or a regression check, read [soak-test design](references/soak-testing.md). Define a repeatable lifecycle with explicit cleanup, keep one isolated server alive across batches, and record both completed work and elapsed time. Use post-warm-up checkpoints and resource inventories to distinguish accumulation from initialization or intentional history. The existing probe covers cycle-driven location/session churn; it does not simulate elapsed server time, provider streams, or timer workloads.

## Interpret and finish

- Attribute large sources with evidence: module/package copies, schema populations, catalog caches, project graphs, native buffers, SQLite, or long-lived queues. Read current cleanup ownership before suggesting changes.
- Distinguish confirmed growth, a reproduced leak, and optimization opportunities. Stable isolated lifecycle memory does not rule out leaks in model streams, tool execution, native libraries, or plugin reloads.
- Preserve a concise report with timestamps/build, initial footprint, snapshot accounting, tested workload and warm-up plateau/deltas, source references, limitations, and artifact paths. Report any live snapshot overhead and the final state of the live service.
- Keep investigation artifacts outside the repository. Remove only redundant artifacts created by this investigation; retain summaries and useful snapshots. Confirm isolated children exited. Do not edit implementation or change cache policies solely because one memory reading looks large.
