# OpenCode-specific investigation notes

Use these as search anchors, not assumptions about every build. Respect the checked-out repository's AGENTS.md. The user's usual checkout is `/Users/guochunzhong/git/oss/opencode`; discover it from task context when different. Do not hardcode a PID, release name, plugin directory, or Node version into a new investigation.

## Source and runtime inventory

Start with a focused search rather than dumping entire minified bundles:

```bash
rg -n 'SIGUSR1|writeHeapSnapshot|memoryUsage|cache_size|idleTimeToLive|timeToLive|bundledCache' packages/cli/src/heap.ts packages/core/src/location* packages/core/src/database/database.ts packages/core/src/models-dev.ts
rg -n 'NODE_VERSION|execArgvExtension|useSnapshot|useCodeCache' packages/cli/script/build-node.ts
rg -n '__opencode_reload|sources =|registerHooks' packages/plugin/src/source* packages/cli/vite.node.config.ts
```

Useful anchors:

| Area | Source |
|---|---|
| Heap snapshot signal | `packages/cli/src/heap.ts`, registration in `packages/cli/src/index.ts` |
| SEA runtime and flags | `packages/cli/script/build-node.ts`, `packages/cli/src/node/target.ts` |
| Server startup / isolation settings | `packages/cli/src/server-process.ts`, `packages/util/src/global.ts` and its roots implementation |
| Service registration and credentials | `packages/cli/src/services/service-config.ts`, `packages/client/src/effect/service.ts` |
| Location inventory and eviction | `packages/protocol/src/groups/debug.ts`, `packages/server/src/handlers/debug.ts` |
| Request location routing | `packages/server/src/location.ts` |
| Plugin readiness | `packages/server/src/handlers/agent.ts` |
| Graph lifetime / cleanup | `packages/core/src/location-services.ts`, `location-activity.ts`, `location-lifecycle.ts` |
| Database cache and native statements | `packages/core/src/database/database.ts`, `sqlite.node.ts` |
| Models catalog | `packages/core/src/models-dev.ts`, `packages/core/src/plugin/models-dev.ts` |
| Plugin module reuse | `packages/plugin/src/source.ts`, `source.node.ts`, `packages/core/src/plugin/module.ts` |
| MCP imports and schemas | `packages/core/src/mcp/client.ts`, `stdio.ts`, `oauth.ts` |

Use `lsof` to identify the actual database, native addons, listening address, and extracted SEA assets. For compiled code, print filenames and short relevant matches; do not print complete minified lines. Use service logs to check actual idle evictions. Ignore logs belonging to older process runs.

## Authenticated read-only location inventory

In the September 2026 build, registration was in `~/.local/state/opencode/service.json`; channel-specific filenames and XDG roots may differ. Confirm its PID matches the target before using the URL. The password may be in registration or the corresponding configuration file. Read only the needed fields, use HTTP Basic authentication with username `opencode`, and keep the password out of stdout, shell arguments and saved reports.

`GET /api/debug/location` lists the keys currently routed by `LocationServiceMap` without booting a graph. Use it to verify eviction and use the heap to check what survives. Do not use `DELETE /api/debug/location` or `/api/location/reload` against the user's live server for an ordinary inventory. Those are mutations; the reproduction helper uses DELETE only against its own isolated server.

The isolated helper assumes:

- `serve --hostname 127.0.0.1 --port 0` prints `server listening on http://127.0.0.1:<port>`.
- `OPENCODE_PASSWORD`, XDG directory overrides, `OPENCODE_TEST_HOME`, `OPENCODE_CONFIG_DIR`, `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_CONFIG_PROJECT_DISABLE`, `OPENCODE_DISABLE_FFF`, and `OPENCODE_FILEWATCHER_DISABLE` have the meanings used in `server-process.ts`.
- `GET /api/agent` and `GET /api/model` open a Location and read its current state. Plugin activation is asynchronous in the investigated November 2026 build. When a reproduction requires a specific plugin, observe it in `GET /api/plugin` or wait for its first expected local event or request.
- Location queries use `location[directory]`; DELETE on `/api/debug/location` evicts that location.
- The optional `session-events` workload uses `GET /api/event` (initial `server.connected` SSE frame), `POST /api/session` with an explicit `location.directory` (response `data.id`), then `DELETE /api/session/:id`. The stream must deliver `session.deleted` with `data.sessionID`. `GET /api/session` and `/api/session/active` return `data` collections that must be empty at checkpoints. No prompt/model execution endpoint is called.
- SIGUSR1 writes a V8 snapshot under the isolated data/log directory and logs `heap snapshot written` with its pathname.

A contract mismatch should stop the probe and preserve its logs. Adapt the helper after inspecting that version; do not fall back to the live registration or user configuration.

For session/subscription attribution, inspect `packages/server/src/event-feed.ts` (subscriber queue ownership and finalizers), `packages/server/src/handlers/event.ts` (SSE stream scope), `packages/core/src/bus.ts` (routing maps, listeners, subscriptions), and `packages/core/src/session/run-coordinator.ts` (execution settlement). An empty location inventory does not imply that process-global maps or active borrowed graphs are empty.

## Authenticated heap capture

Where the installed build exposes `POST /api/debug/heap-dump`, use the same PID-verified loopback URL and in-memory Basic authentication as for inventory. The optional `directory` query parameter accepts an absolute path. Prefer a private output directory and check the returned PID and path against the target and the server's completion log. Inspect `packages/protocol/src/groups/debug.ts` and `packages/server/src/handlers/debug.ts` for build-specific contracts. This endpoint pauses the target just like signal-based capture. The bundled isolated probe still uses its verified signal handler.

## Evicted root and Console timer example

In the November 2026 Node SEA investigation, an evicted `/` Location descriptor survived in `Shell.Daemon.places`. The path ran through the cached entry's `runs` interface and a closure that captured the Location. Its shell-run map was empty. The isolated run reproduced the same descriptor surviving eviction and the timer deadline. The scoped `/` filesystem service was already gone by the first post-eviction heap. Inspect `place`, `at`, and `places` in `packages/core/src/shell.ts` when a Location object has this owner.

The 60-second timer in the same live heap was the OpenCode Console configuration poll for a still-routed home Location. The fiber's filesystem closure was identical to the one in the home routing entry. With both Locations in an isolated server, the home tick replaced its timeout and kept the same fiber and filesystem closure. Eviction canceled the corresponding Location's poll. Inspect `poll`, its scoped `FiberHandle`, and the installed callback in `packages/core/src/plugin/provider/opencode.ts`. A synthetic credential pointed at a loopback Console fixture can exercise this polling path without a model request or user credentials.

## Plugin attribution

First inspect loaded script/package roots and reload generation counts in the heap summary. Multiple copies of Effect or Zod, TypeScript stripping machinery, and MCP schema objects can explain retained memory even when no user task is running. Module source bytes understate the full runtime object cost.

For an import-only experiment, inspect the plugin module's top-level behavior and all relevant imports. A wrapper can import the real definition and export a copy with its activation replaced by a no-op Effect. Merely importing a plugin can still perform top-level I/O; replacing activation is not a sandbox. Use an isolated probe with a temporary wrapper only when that inspected module graph is appropriate. Never copy the user's entire configuration, credentials, or session database merely to measure dependency loading.

Do not run the original plugin's activation automatically: the investigated desktop plugin installed files into SketchUp during activation. Separate import cost, activation cost, and tool workload cost in the result. Prefer compiled plugin imports and dependency deduplication as candidates to measure, not fixes asserted in advance.

Changed query strings create distinct Node ESM module identities. Count generations per source before blaming hot reload. Unchanged-source reuse can work correctly even though imported modules outlive evicted project graphs.

## Prior measurements: context, not expected results

A 2026-09-19 investigation of the Node 26.8.2 SEA dev build found 228 MiB pre-profiling physical footprint and 138.5 MiB snapshot-accounted memory. A fresh isolated copy measured 129.8 MiB physical footprint. Full project initialization/eviction plateaued at 68.7, 69.3, and 68.8 MiB at 10, 30, and 100 cycles. Importing one desktop plugin graph added about 26 MiB. These do not establish a universal acceptable baseline.

The live snapshot took approximately 1.8 seconds but raised peak physical footprint to 1.2 GiB and left it around 379 MiB at the end. Save the initial numbers first; prefer isolated probes for repeated snapshots. macOS allocator retention and profiler allocations can contaminate post-snapshot RSS long after the captured workload finishes.

Authoritative references when interpretation needs verification:

- [Node memory accounting](https://nodejs.org/api/process.html#processmemoryusage)
- [Heap snapshot operation and overhead](https://nodejs.org/learn/diagnostics/memory/using-heap-snapshot)
- [Node ESM URL identity and caching](https://nodejs.org/api/esm.html#urls)
