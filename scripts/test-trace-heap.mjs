import { test } from 'node:test';
import { strict } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

test('real V8 snapshots identify a surviving allocation, its owner, and later release', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'opencode-heap-test-'));
  fs.chmodSync(root, 0o700);
  try {
    fs.writeFileSync(path.join(root, 'fixture.mjs'), `
      import { writeHeapSnapshot } from 'node:v8';
      const held = [];
      class MemoryProbeFixture { constructor() { this.payload = 'private-fixture-payload-do-not-emit'; } }
      function allocate() { held.push(new MemoryProbeFixture(), new MemoryProbeFixture()); }
      function releaseOne() { held.pop(); }
      writeHeapSnapshot('a.heapsnapshot');
      allocate();
      writeHeapSnapshot('b.heapsnapshot');
      releaseOne();
      await new Promise(resolve => setImmediate(resolve));
      writeHeapSnapshot('c.heapsnapshot');
      held.length = 0;
      await new Promise(resolve => setImmediate(resolve));
      writeHeapSnapshot('d.heapsnapshot');
      console.log(held.length);
    `, { mode: 0o600 });
    const fixture = spawnSync(process.execPath, ['fixture.mjs'], { cwd: root, encoding: 'utf8', timeout: 60000 });
    strict.equal(fixture.status, 0, fixture.stderr);
    const script = fileURLToPath(new URL('./trace-heap.mjs', import.meta.url));
    const args = ['--same-isolate', '--name', 'MemoryProbeFixture'];
    const run = spawnSync(process.execPath, [script, 'a.heapsnapshot', 'b.heapsnapshot', 'c.heapsnapshot', ...args, '--out', 'retained.json'],
      { cwd: root, encoding: 'utf8', timeout: 60000 });
    strict.equal(run.status, 0, run.stderr);
    const raw = fs.readFileSync(path.join(root, 'retained.json'), 'utf8');
    const report = JSON.parse(raw);
    strict.equal(report.survivors.find(entry => entry.name === 'object: MemoryProbeFixture')?.count, 1);
    const retained = report.paths.find(entry => entry.target.type === 'object');
    strict.ok(retained.path.length > 1);
    strict.ok(retained.path.some(entry => entry.node.name === 'Array'), JSON.stringify(retained.path));
    strict.equal(retained.path.at(-1).node.name, 'MemoryProbeFixture');
    strict.ok(!raw.includes('private-fixture-payload-do-not-emit'));
    strict.equal(fs.statSync(path.join(root, 'retained.json')).mode & 0o777, 0o600);
    const released = spawnSync(process.execPath, [script, 'a.heapsnapshot', 'b.heapsnapshot', 'd.heapsnapshot', ...args, '--out', 'released.json'],
      { cwd: root, encoding: 'utf8', timeout: 60000 });
    strict.equal(released.status, 0, released.stderr);
    strict.ok(!JSON.parse(fs.readFileSync(path.join(root, 'released.json'))).survivors.some(entry => entry.name === 'object: MemoryProbeFixture'));
    const refused = spawnSync(process.execPath, [script, 'a.heapsnapshot', 'b.heapsnapshot', 'c.heapsnapshot', '--out', 'unsafe.json'],
      { cwd: root, encoding: 'utf8' });
    strict.notEqual(refused.status, 0);
    strict.ok(!fs.existsSync(path.join(root, 'unsafe.json')));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
