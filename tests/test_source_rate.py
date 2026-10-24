import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from source_rate import claim_slot, locked_state, foreground, cooldown, release_priority


def increment(root):
    for _ in range(30):
        with locked_state(Path(root)) as state:
            state['count'] = state.get('count', 0)+1


class SourceRateTests(unittest.TestCase):
    def test_cpu_preparation_releases_background_without_clearing_rate_limits(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, IONOSCOPE_GIRO_RATE_DIR=directory):
            with foreground(100):
                cooldown(60)
                release_priority()
                state=json.loads((Path(directory)/'state.json').read_text())
                self.assertEqual(state['priority_until'],0)
                self.assertGreater(state['blocked_until'],0)

    def test_spacing_cooldown_and_priority_share_the_same_gate(self):
        state = {}
        self.assertEqual(claim_slot(state, 100), 0)
        self.assertEqual(claim_slot(state, 102), 4)
        state.update(blocked_until=160, priority_until=200)
        self.assertEqual(claim_slot(state, 150), 10)
        self.assertEqual(claim_slot(state, 160, background=True), 5)
        self.assertEqual(claim_slot(state, 160), 0)
        self.assertEqual(state['next_request'], 166)

    def test_processes_do_not_lose_shared_updates(self):
        with tempfile.TemporaryDirectory(dir=os.getenv('IONOSCOPE_TEST_TMPDIR')) as directory:
            processes = [multiprocessing.Process(target=increment, args=(directory,)) for _ in range(4)]
            for process in processes: process.start()
            for process in processes:
                process.join(30)
                if process.is_alive(): process.kill(); process.join(); self.fail('Rate lock deadlocked')
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(json.loads((Path(directory)/'state.json').read_text())['count'], 120)

    def test_failed_foreground_releases_priority_but_retains_cooldown(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, IONOSCOPE_GIRO_RATE_DIR=directory):
            with self.assertRaises(RuntimeError):
                with foreground(100):
                    cooldown(60)
                    raise RuntimeError('failed collection')
            state = json.loads((Path(directory)/'state.json').read_text())
            self.assertEqual(state['priority_until'], 0)
            self.assertGreater(state['blocked_until'], 0)

    def test_corrupt_rate_state_does_not_allow_unpaced_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'state.json').write_text('{broken')
            with self.assertRaises(ValueError):
                with locked_state(Path(directory)): pass
