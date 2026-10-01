import json
from pathlib import Path
import tempfile
import unittest
from artifact_export import export_artifacts


class ExportTest(unittest.TestCase):
    def test_checkpoint_is_allowlisted_and_refreshes_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / 'run', Path(directory) / 'out'
            root.mkdir()
            (root / 'key.pem').write_text('private')
            (root / 'world.json').write_text('raw')
            (root / 'verification-report.json').write_text('{"status":"failed"}')
            arm = root / 'arm-kill'
            arm.mkdir()
            (arm / 'result.json').write_text('{"ok":true}')
            export_artifacts(root, output)
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                             {'verification-report.json', 'arm-kill/result.json'})
            (root / 'verification-report.json').write_text('{"status":"passed"}')
            export_artifacts(root, output)
            self.assertEqual(json.loads((output / 'verification-report.json').read_text())['status'], 'passed')
