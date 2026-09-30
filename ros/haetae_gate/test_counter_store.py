import json
from pathlib import Path
import tempfile
import unittest
from counter_store import CounterStore


class CounterStoreTests(unittest.TestCase):
    def test_exclusive_counter_reservation_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'counters'
            store = CounterStore(target)
            self.addCleanup(store.close)
            store.reserve(7, {'vla': 12})
            self.assertEqual(json.loads(target.read_text()), {'auth_epoch': 7, 'counters': {'vla': 12}})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(BlockingIOError): CounterStore(target)
            target.chmod(0o644)
            with self.assertRaises(ValueError): CounterStore(target)

    def test_counter_path_cannot_be_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'counter'
            target.symlink_to(Path(directory) / 'missing')
            with self.assertRaises(ValueError): CounterStore(target)
