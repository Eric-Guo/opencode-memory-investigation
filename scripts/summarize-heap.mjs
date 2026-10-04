#!/usr/bin/env node
// Pure offline analysis: never evaluates captured source or prints string values.
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
if (args.includes('--help') || args.length === 0) {
  console.log('Usage: node summarize-heap.mjs snapshot... [--out new-summary.json]');
  process.exit(args.length ? 0 : 1);
}
const outputIndex = args.indexOf('--out');
const output = outputIndex < 0 ? undefined : args[outputIndex + 1];
if (outputIndex >= 0 && (!output || outputIndex !== args.length - 2)) throw new Error('--out requires a final filename');
const inputs = outputIndex < 0 ? args : args.slice(0, outputIndex);
if (!inputs.length) throw new Error('At least one heap snapshot is required');

function summarize(file) {
  const h = JSON.parse(fs.readFileSync(file, 'utf8'));
  const meta = h.snapshot.meta;
  const nf = meta.node_fields.length, ef = meta.edge_fields.length;
  const type = meta.node_fields.indexOf('type'), name = meta.node_fields.indexOf('name');
  const size = meta.node_fields.indexOf('self_size'), count = meta.node_fields.indexOf('edge_count');
  const ename = meta.edge_fields.indexOf('name_or_index'), to = meta.edge_fields.indexOf('to_node');
  const etype = meta.edge_fields.indexOf('type');
  if ([type, name, size, count, ename, to, etype].some(i => i < 0) || h.nodes.length % nf || h.edges.length % ef)
    throw new Error(`Unsupported V8 snapshot schema: ${file}`);
  const types = meta.node_types[type], edgeTypes = meta.edge_types[etype];
  const byType = new Map(), constructors = new Map(), packages = new Map(), reloads = new Map();
  const sources = [], sourceNodes = new Set();
  let total = 0, edge = 0, sourceBytes = 0;
  const add = (map, key, bytes) => {
    const value = map.get(key) ?? { name: key, count: 0, bytes: 0 };
    value.count++; value.bytes += bytes; map.set(key, value);
  };
  for (let i = 0; i < h.nodes.length; i += nf) {
    const kind = types[h.nodes[i + type]], label = h.strings[h.nodes[i + name]], bytes = h.nodes[i + size];
    total += bytes;
    add(byType, kind, bytes);
    if (['object', 'closure', 'array', 'native', 'hidden', 'object shape'].includes(kind))
      add(constructors, `${kind}: ${label}`, bytes);
    if (kind === 'code' && (label.startsWith('file:') || label.startsWith('node:'))) {
      const url = new URL(label);
      const clean = label.startsWith('file:') ? `file://${url.host}${url.pathname}` : label.split('?')[0];
      const version = url.searchParams.get('__opencode_reload');
      if (version !== null) {
        const values = reloads.get(clean) ?? new Set(); values.add(version); reloads.set(clean, values);
      }
      let bytes = 0;
      for (let e = edge; e < edge + h.nodes[i + count] * ef; e += ef) {
        if (edgeTypes[h.edges[e + etype]] !== 'internal' || h.strings[h.edges[e + ename]] !== 'source') continue;
        const node = h.edges[e + to]; bytes += h.nodes[node + size];
        if (!sourceNodes.has(node)) { sourceNodes.add(node); sourceBytes += h.nodes[node + size]; }
      }
      sources.push({ script: clean, source_bytes: bytes });
      const marker = clean.lastIndexOf('/node_modules/');
      const suffix = marker < 0 ? '' : clean.slice(marker + 14);
      const packageName = suffix.startsWith('@') ? suffix.split('/').slice(0, 2).join('/') : suffix.split('/')[0];
      const group = marker >= 0 ? clean.slice(0, marker + 14) + packageName : label.startsWith('node:') ? 'Node built-ins' : 'Application files';
      add(packages, group, bytes);
    }
    edge += h.nodes[i + count] * ef;
  }
  if (edge !== h.edges.length) throw new Error(`Inconsistent edge counts: ${file}`);
  const sorted = map => [...map.values()].sort((a, b) => b.bytes - a.bytes);
  return {
    file: path.resolve(file), serialized_bytes: fs.statSync(file).size,
    snapshot_accounted_bytes: total, snapshot_accounted_mib: total / 1048576,
    nodes: h.nodes.length / nf, edges: h.edges.length / ef,
    types: sorted(byType), constructors: sorted(constructors),
    unique_script_source_bytes: sourceBytes, packages: sorted(packages),
    largest_script_sources: sources.sort((a, b) => b.source_bytes - a.source_bytes).slice(0, 30),
    reload_generations: [...reloads].map(([script, values]) => ({script, generations: values.size})),
  };
}
const summaries = inputs.map(summarize);
const deltas = summaries.slice(1).map((after, i) => {
  const before = summaries[i], previous = new Map(before.constructors.map(value => [value.name, value]));
  const current = new Map(after.constructors.map(value => [value.name, value]));
  const names = new Set([...previous.keys(), ...current.keys()]);
  return {
    before: before.file, after: after.file,
    snapshot_accounted_bytes: after.snapshot_accounted_bytes - before.snapshot_accounted_bytes,
    nodes: after.nodes - before.nodes,
    largest_constructor_changes: [...names].map(name => ({name,
      bytes: (current.get(name)?.bytes ?? 0) - (previous.get(name)?.bytes ?? 0),
      count: (current.get(name)?.count ?? 0) - (previous.get(name)?.count ?? 0),
    })).sort((a, b) => Math.abs(b.bytes) - Math.abs(a.bytes)).slice(0, 25),
  };
});
const report = { accounting: 'Sum of snapshot node self_size, including reported native nodes; not heapUsed or RSS. Constructor bytes are shallow, not retained.', summaries, deltas };
if (output) fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
console.log(JSON.stringify({
  output: output && path.resolve(output),
  summaries: summaries.map(s => ({file:s.file, mib:s.snapshot_accounted_mib, nodes:s.nodes, top_types:s.types.slice(0,6), packages:s.packages})),
  deltas,
}, null, 2));
