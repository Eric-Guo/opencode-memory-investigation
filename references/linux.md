# Linux process accounting and bounded probes

Use the same isolated lifecycle probe on Linux after verifying current endpoint and SIGUSR1 contracts. Preserve macOS behavior: vmmap physical footprint remains macOS-only; Linux leaves that field null and adds independently named metrics.

- `/proc/PID/status`: VmRSS, VmHWM (peak RSS), VmSize (virtual reservation), RssAnon, RssFile, RssShmem and VmSwap
- `/proc/PID/smaps_rollup`: RSS, PSS (shared pages proportionally charged), private clean/dirty and Anonymous; absent permission is recorded rather than silently treated as zero
- `/proc/PID/fd` and `task`: instantaneous open descriptors and thread counts; these are resource inventories, not attribution

Check `/proc/PID/status` State as well as existence. The sampler records `process_state` and `is_zombie`; a zombie can satisfy kill(pid, 0) while having no live RSS. Preserve missing measurements as null rather than turning them into zeros. PID and net namespaces can differ between cloud executions despite shared files. Sample from the owner's namespace; do not attach, kill, restart, or infer death from another namespace's PID table. SQLite locking observed in same-kernel unshare tests does not establish real cross-execution or NFS locking guarantees.

Status and smaps are separate non-atomic samples and may differ slightly. Resident file mappings are not native heap. Anonymous pages include V8 and other native allocators. Never derive native allocation size by subtracting snapshot totals from RSS. `heapUsed`, `heapTotal`, `external` and `arrayBuffers` require runtime diagnostics from this exact process; mark them unavailable when none exist. V8 snapshot self-sizes are snapshot-accounted bytes, not those runtime counters.

The probe whitelists PATH and locale only, sets temporary HOME/TMP/XDG roots and generates a disposable server password. Never copy production auth/config into it. Snapshots may contain this temporary password and fixture data; keep raw heaps private and local. No genuine user secrets are needed for location/session-events workloads.

Use bounded workloads (for example 10,30,60,100 cycles), repeated unprofiled controls and consistent settle time. The watchdog polls child RSS every 250 ms and terminates only that child on total deadline or sampled RSS ceiling. It is not a hard kernel limit, cannot prevent a rapid allocation spike and does not measure subprocess RSS. Keep concurrency low and leave large headroom for snapshots and offline analysis. Confirm children exited in results.json; failures retain evidence.

Run `python3 scripts/test-linux.py` and `node --test scripts/test-trace-heap.mjs` before installing adaptations. Linux tests include real proc accounting, process exit, private output, isolated environment and guaranteed own-child termination on a bounded startup failure.

Upstream source: https://github.com/Eric-Guo/opencode-memory-investigation at 90b6bf9c92b2bc20ed77609cbd20da6dfd833c1a (MIT, included LICENSE). Linux additions are local adaptations; do not imply upstream publication.
