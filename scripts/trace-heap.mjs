#!/usr/bin/env node
// Offline A/B/C cohort analysis. Never evaluates captured code or emits string-node values.
import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: 'string' }, name: { type: 'string' }, limit: { type: 'string', default: '5' },
    'same-isolate': { type: 'boolean' }, help: { type: 'boolean' },
  },
});
if (values.help) {
  console.log('Usage: node trace-heap.mjs A.heapsnapshot B.heapsnapshot C.heapsnapshot --same-isolate --out new.json [--name Constructor] [--limit 5]');
  process.exit(0);
}
const limit = Number(values.limit);
if (positionals.length !== 3 || !values.out || !values['same-isolate'] || !Number.isInteger(limit) || limit < 1 || limit > 50)
  throw new Error('Require chronological A/B/C from ONE uninterrupted V8 isolate, --same-isolate, --out, and limit 1..50. Use --help.');
if (new Set(positionals.map(file => fs.realpathSync(file))).size !== 3)
  throw new Error('Use three distinct snapshots');

function read(file) {
  const heap = JSON.parse(fs.readFileSync(file, 'utf8'));
  const meta = heap.snapshot.meta;
  const fields = Object.fromEntries(['type', 'name', 'id', 'self_size', 'edge_count'].map(name => [name, meta.node_fields.indexOf(name)]));
  const edges = Object.fromEntries(['type', 'name_or_index', 'to_node'].map(name => [name, meta.edge_fields.indexOf(name)]));
  const width = meta.node_fields.length, edgeWidth = meta.edge_fields.length;
  if (Object.values(fields).concat(Object.values(edges)).some(index => index < 0) ||
      heap.nodes.length % width || heap.edges.length % edgeWidth ||
      heap.snapshot.node_count !== heap.nodes.length / width || heap.snapshot.edge_count !== heap.edges.length / edgeWidth)
    throw new Error(`Unsupported or incomplete V8 snapshot: ${file}`);
  return { heap, fields, edges, width, edgeWidth, types: meta.node_types[fields.type], edgeTypes: meta.edge_types[edges.type] };
}

function kind(graph, offset) {
  return graph.types[graph.heap.nodes[offset + graph.fields.type]];
}

function label(graph, offset) {
  const type = kind(graph, offset);
  // Names of strings contain captured application data, unlike constructor labels.
  return type.includes('string') ? '[string omitted]' : graph.heap.strings[graph.heap.nodes[offset + graph.fields.name]];
}

function index(graph) {
  const ids = new Map(), groups = new Map();
  for (let offset = 0; offset < graph.heap.nodes.length; offset += graph.width) {
    const type = kind(graph, offset);
    if (!['object', 'closure', 'array', 'native'].includes(type)) continue;
    const name = label(graph, offset);
    if (values.name && name !== values.name) continue;
    const group = `${type}: ${name}`;
    const bytes = graph.heap.nodes[offset + graph.fields.self_size];
    ids.set(graph.heap.nodes[offset + graph.fields.id], { group, bytes, offset });
    const entry = groups.get(group) ?? { count: 0, bytes: 0 };
    entry.count++; entry.bytes += bytes; groups.set(group, entry);
  }
  return { ids, groups };
}

// Keep compact indexes for A/B; release their full graphs before loading C.
const before = index(read(positionals[0]));
const after = index(read(positionals[1]));
const graph = read(positionals[2]);
const later = index(graph);
const cohorts = new Map();
for (const [id, entry] of after.ids) {
  if (before.ids.has(id)) continue;
  const current = later.ids.get(id);
  if (!current || current.group !== entry.group) continue;
  const cohort = cohorts.get(entry.group) ?? { name: entry.group, count: 0, shallow_bytes: 0, examples: [] };
  cohort.count++; cohort.shallow_bytes += current.bytes;
  if (cohort.examples.length < limit) cohort.examples.push(current.offset);
  cohorts.set(entry.group, cohort);
}
const selected = [...cohorts.values()].sort((a, b) => b.shallow_bytes - a.shallow_bytes || b.count - a.count);
const targets = new Set(selected.slice(0, limit).flatMap(cohort => cohort.examples.slice(0, 2)));

// Breadth-first traversal finds one shortest candidate path from the synthetic root.
// This is deliberately not a dominator/retained-size implementation.
const count = graph.heap.nodes.length / graph.width;
const starts = new Uint32Array(count + 1);
for (let i = 0; i < count; i++) starts[i + 1] = starts[i] + graph.heap.nodes[i * graph.width + graph.fields.edge_count] * graph.edgeWidth;
if (starts[count] !== graph.heap.edges.length) throw new Error('Inconsistent snapshot edge counts');
const parent = new Int32Array(count).fill(-1), via = new Int32Array(count).fill(-1), queue = new Uint32Array(count);
parent[0] = 0;
let head = 0, tail = 1;
while (head < tail) {
  const node = queue[head++];
  for (let edge = starts[node]; edge < starts[node + 1]; edge += graph.edgeWidth) {
    const type = graph.edgeTypes[graph.heap.edges[edge + graph.edges.type]];
    if (type === 'weak') continue;
    const offset = graph.heap.edges[edge + graph.edges.to_node];
    if (!Number.isInteger(offset) || offset % graph.width || offset < 0 || offset >= graph.heap.nodes.length)
      throw new Error('Invalid snapshot edge target');
    const child = offset / graph.width;
    if (parent[child] !== -1) continue;
    parent[child] = node; via[child] = edge; queue[tail++] = child;
  }
}

function describe(offset) {
  return { id: graph.heap.nodes[offset + graph.fields.id], type: kind(graph, offset), name: label(graph, offset),
    shallow_bytes: graph.heap.nodes[offset + graph.fields.self_size] };
}

function retainingPath(offset) {
  const chain = [];
  let node = offset / graph.width;
  if (parent[node] === -1) return { target: describe(offset), status: 'No path found after excluding weak edges', path: [] };
  while (node !== 0) {
    const edge = via[node], type = graph.edgeTypes[graph.heap.edges[edge + graph.edges.type]];
    const name = graph.heap.edges[edge + graph.edges.name_or_index];
    chain.push({ node: describe(node * graph.width), edge_from_parent: {
      type, name: ['element', 'hidden'].includes(type) ? name : graph.heap.strings[name],
    } });
    node = parent[node];
  }
  chain.push({ node: describe(0) });
  return { target: describe(offset), status: 'Candidate path; verify conditional/ephemeron edges in a heap viewer', path: chain.reverse() };
}

const groups = new Set([...before.groups.keys(), ...after.groups.keys(), ...later.groups.keys()]);
const changes = [...groups].map(name => {
  const a = before.groups.get(name) ?? { count: 0, bytes: 0 };
  const b = after.groups.get(name) ?? { count: 0, bytes: 0 };
  const c = later.groups.get(name) ?? { count: 0, bytes: 0 };
  return { name, counts: [a.count, b.count, c.count], shallow_bytes: [a.bytes, b.bytes, c.bytes],
    count_deltas: [b.count - a.count, c.count - b.count], byte_deltas: [b.bytes - a.bytes, c.bytes - b.bytes] };
}).sort((a, b) => Math.abs(b.byte_deltas[1]) - Math.abs(a.byte_deltas[1]));
const report = {
  files: positionals.map(file => path.resolve(file)), same_isolate_asserted: true,
  accounting: 'Objects allocated between A and B still present in C, matched by V8 node ID. Shallow sizes only, not retained sizes. Persistence may be expected caching; this is not a leak verdict.',
  path_limitations: 'One shortest path per example, excluding weak edges. Conditional ephemeron/internal edges are not modeled. Property/constructor names may be sensitive; output is private and is not a sanitized public artifact.',
  selection: values.name ?? 'object, closure, array, native',
  survivors: selected.map(cohort => ({ name: cohort.name, count: cohort.count, shallow_bytes: cohort.shallow_bytes })),
  changes, paths: [...targets].map(retainingPath),
};
fs.writeFileSync(values.out, JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
console.log(JSON.stringify({ output: path.resolve(values.out), survivor_groups: selected.length, paths: targets.size }));
