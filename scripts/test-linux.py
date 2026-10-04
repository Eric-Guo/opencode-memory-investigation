#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('memory', SCRIPTS / 'process-memory.py')
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)

@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux proc tests')
class LinuxTests(unittest.TestCase):
    def test_proc(self):
        value = memory.linux_memory(os.getpid())
        self.assertGreater(value['rss_mib'], 0)
        self.assertGreater(value['pss_mib'], 0)
        self.assertGreaterEqual(value['threads'], 1)
        self.assertGreaterEqual(value['file_descriptors'], 3)
        self.assertNotIn('physical_footprint_mib', value)
    def test_missing_process(self):
        with self.assertRaises(FileNotFoundError):
            memory.linux_memory(2147483647)
    def test_parser(self):
        self.assertEqual(memory.kb_fields('Rss: 2048 kB\nThreads: 2\n'), {'Rss': 2})
    def test_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'sample'
            p = subprocess.run([sys.executable, str(SCRIPTS / 'sample-process.py'), str(os.getpid()), '--out', str(out), '--samples', '1'], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertGreater(json.loads((out / 'samples.json').read_text())[0]['rss_mib'], 0)
    def test_budget_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-server'
            binary.write_text('#!/usr/bin/env python3\nimport os,json,time\nprint(json.dumps({k:os.environ.get(k) for k in ["HOME","TMPDIR","TEST_SECRET","XDG_DATA_HOME"]}),flush=True)\ntime.sleep(60)\n')
            binary.chmod(0o700)
            out = root / 'probe'
            p = subprocess.run([sys.executable, str(SCRIPTS / 'isolated-probe.py'), '--binary', str(binary), '--out', str(out), '--snapshots', 'none', '--timeout', '5', '--max-seconds', '.5'], env={**os.environ, 'TEST_SECRET': 'MUST_NOT_INHERIT'}, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)
            result = json.loads((out / 'results.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['budget_stop']['reason'], 'total deadline')
            self.assertIsNotNone(result['child_exit_code'])
            captured = json.loads((out / 'stdout.log').read_text())
            self.assertEqual(captured['HOME'], str(out / 'home'))
            self.assertEqual(captured['TMPDIR'], str(out / 'tmp'))
            self.assertIsNone(captured['TEST_SECRET'])
            self.assertFalse(Path('/proc', str(result['pid'])).exists())

if __name__ == '__main__':
    unittest.main()
