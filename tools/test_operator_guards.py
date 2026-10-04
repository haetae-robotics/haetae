import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench_provenance import require_verified_binary
from safe_stage import prepare_stage, MARKER


class OperatorGuardTest(unittest.TestCase):
    def test_stage_rejects_source_home_link_and_unmarked_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, stage = root / 'repo', root / 'stage'
            repo.mkdir()
            for forbidden in (repo, repo / 'sim', root, Path.home(), Path('/')):
                with self.subTest(path=forbidden), self.assertRaises(ValueError):
                    prepare_stage(forbidden, repo)
            stage.mkdir()
            (stage / 'user-file').write_text('preserve')
            with self.assertRaises(ValueError):
                prepare_stage(stage, repo)
            self.assertEqual((stage / 'user-file').read_text(), 'preserve')
            (stage / 'user-file').unlink()
            self.assertEqual(prepare_stage(stage, repo), stage)
            self.assertTrue((stage / MARKER).is_file())
            self.assertEqual(prepare_stage(stage, repo), stage)
            link = root / 'link'
            link.symlink_to(stage, target_is_directory=True)
            with self.assertRaises(ValueError):
                prepare_stage(link, repo)

    def test_bench_requires_exact_successful_clean_snapshot_and_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'haetae'
            binary.write_bytes(b'synthetic binary')
            report_path = root / 'artifacts/bench/report.json'
            report_path.parent.mkdir(parents=True)
            valid = {'passed': True, 'source_revision': 'a' * 40, 'source_dirty': False,
                     'firmware_sha256': {}, 'gate_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
            def git_output(argv, **kwargs):
                return 'a' * 40 if argv[1] == 'rev-parse' else ''
            with patch('bench_provenance.subprocess.check_output', side_effect=git_output):
                report_path.write_text(json.dumps(valid))
                self.assertEqual(require_verified_binary(root, binary), valid)
                for change in ({'passed': False}, {'source_revision': 'b' * 40},
                               {'source_dirty': True}, {'gate_sha256': 'b' * 64},
                               {'firmware_sha256': {'absent.h': 'b' * 64}}):
                    with self.subTest(change=change):
                        report_path.write_text(json.dumps({**valid, **change}))
                        with self.assertRaises(RuntimeError):
                            require_verified_binary(root, binary)
                report_path.write_text(json.dumps(valid))
                binary.write_bytes(b'changed binary')
                with self.assertRaises(RuntimeError):
                    require_verified_binary(root, binary)
