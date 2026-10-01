import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from check_live_stream import run


class LiveCheckerTest(unittest.TestCase):
    def test_outer_timeout_fails_and_preserves_partial_stream(self):
        stream = io.BytesIO(b'data: {"kind":"phase","label":"started"}\n'
                            b'data: {"kind":"telemetry"}\n')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'partial.jsonl'
            with patch('check_live_stream.urlopen', return_value=stream), \
                    patch('check_live_stream.time.monotonic', side_effect=[0, .1, 2]):
                with self.assertRaisesRegex(TimeoutError, 'did not finish'):
                    run(8765, output, timeout_seconds=1)
            self.assertEqual([json.loads(line) for line in output.read_text().splitlines()],
                             [{'kind': 'phase', 'label': 'started'}])
