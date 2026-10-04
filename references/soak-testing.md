# Soak tests for OpenCode lifecycles

Read this when a short lifecycle probe cannot explain long-session growth, or when turning a reproduced leak into a regression check.

## Source principle and scope

[Den Odell's “Your SPA Is Leaking Memory. Soak Test It”](https://denodell.com/blog/your-spa-is-leaking-memory-soak-test-it) motivates repeating a round trip in one persistent context, warming it before measurement, and checking resource counts alongside heap size. Fast interaction loops do not necessarily exercise slow timers. Simulated time requires coordinated network responses and representative payloads. Its browser node/listener thresholds and double garbage-collection technique are examples, not portable server criteria.

The following is an OpenCode-specific adaptation. Preserve the skill's unprofiled physical-memory control and isolated-server boundaries. Browser CDP counters do not measure Node server ownership, and a browser fake clock does not advance server timers. If the reported growth is in the desktop renderer, identify that process separately before choosing browser instrumentation.

## Define what one completed cycle releases

Choose a workload from current source and state its expected end inventory:

| Workload | Completion and cleanup evidence |
|---|---|
| Location initialization/eviction | Initialization finishes, eviction finishes, loaded-location inventory returns to baseline |
| Session and SSE churn | Session deletion observed, client stream closed, sessions/executions return to baseline; inspect subscriber finalizers if counts still grow |
| Model cancellation | Local provider fixture observes cancellation; execution settles and stream/queue ownership releases |
| Tool or plugin lifecycle | Inspected tool child/resources close, or plugin scope finalizers complete; intentional module cache remains separately accounted |

Use temporary projects and deterministic local fixtures for added workloads. Deleting test sessions does not prove every database row, cache, or process-global subscriber disappeared. Specify which state may legitimately persist and measure it separately. A test that intentionally accumulates conversation history needs an expected growth model before interpreting its memory slope.

Keep the PID and configuration fixed across batches. Record warm-up separately, then sample several equivalent post-cleanup checkpoints. Require completion evidence with a bounded deadline; a sleep alone cannot prove a finalizer ran. Record lifecycle failures instead of comparing an unfinished cycle with a clean baseline.

## Cover elapsed time as well as churn

Inspect the relevant eviction, retry, heartbeat, or polling intervals. Choose a wall-time window that exercises the suspected callback repeatedly, with an idle control of comparable duration. Record actual callback/request/event completions; cycle count alone cannot establish timer coverage.

For an isolated test harness with an injectable clock, advance the clock used by the suspected server component and await application processing and rescheduling before the next tick. Use controlled response sizes, stream chunking, cancellation and backpressure when those are the suspected owners. Virtual time does not accelerate real I/O or native cleanup; keep a real-time reproduction when those matter.

`isolated-probe.py --checkpoints` specifies cumulative cycle counts. Its `--settle` delay applies at checkpoints, not every cycle, and `--timeout` bounds startup and individual snapshot operations, not total soak duration. These flags do not implement a timer simulator or a total run budget. Add a workload-specific harness only when needed; give it a total deadline and guaranteed child cleanup.

## Interpret and retain a regression

Record timestamp, PID/build, completed cycles, real elapsed time, relevant resource inventories, and measurement mode at checkpoints. Compare post-warm-up deltas per completed cycle and per unit time. Calibrate any absolute tolerance from repeated healthy controls with equivalent work and cleanup; do not copy a browser DOM allowance into server assertions.

Bound the run by planned work/duration and an environment-appropriate memory ceiling. A ceiling breach stops the isolated test and preserves evidence; it does not identify the retaining owner. Keep workloads and budgets explicit before choosing CI or overnight execution; this guidance does not itself schedule recurring work.

When accumulation repeats, use [A/B/C survivor attribution](leak-attribution.md) in a separate profiled run. A useful regression reproduces the old owner's accumulation and shows its release after the fix under the same workload. Stable resource counts leave native growth unresolved; rising footprint alone still needs attribution.
