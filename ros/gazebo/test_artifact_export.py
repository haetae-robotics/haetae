import json
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
from artifact_export import export_artifacts, final_export


class ExportTest(unittest.TestCase):
    def test_export_rejection_preserves_household_scope_and_progress(self):
        from public_report import report
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / 'run', Path(directory) / 'out'
            root.mkdir()
            row = {"id": "human", "blocked": True, "allowed": True,
                   "reason": "human:protected-volume", "denied_drift_rad": .001,
                   "measured_motion_rad": .3, "plan_sha256": "a" * 64,
                   "signed_arm_acceptance_observed": True, "accepted_waypoints_match": True,
                   "max_joint_tracking_error_rad": .01, "tracking_samples": 20}
            (root / 'hazard-progress.json').write_text(json.dumps([row]))
            (root / 'verification-report.json').write_text(json.dumps(report({"profile": "household_hazards"})))
            (root / 'gate.log').symlink_to(root / 'absent')
            with self.assertRaises(ValueError):
                final_export(root, output)
            summary = json.loads((output / 'verification-report.json').read_text())
            self.assertEqual(summary['scope'], 'household_hazard_preflight_simulation')
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['checks'][0]['status'], 'passed')
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

    def test_final_export_preserves_existing_failure_but_rejects_success(self):
        with patch('artifact_export.export_artifacts', side_effect=ValueError('unsafe diagnostic')):
            with patch('artifact_export.sys.stderr'):
                self.assertFalse(final_export('synthetic', None, prior_failure=True))
            with self.assertRaises(ValueError):
                final_export('synthetic', None)

    def test_rejection_still_exports_diagnostics_and_invalidates_old_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / 'run', Path(directory) / 'out'
            root.mkdir()
            output.mkdir()
            (root / 'result.json').symlink_to(root / 'absent')
            (root / 'error.json').write_text('{"error":"synthetic failure"}')
            (root / 'verification-report.json').write_text('{"status":"passed"}')
            with self.assertRaises(ValueError):
                final_export(root, output)
            self.assertEqual(json.loads((output / 'error.json').read_text())['error'], 'synthetic failure')
            self.assertEqual(json.loads((output / 'verification-report.json').read_text())['status'], 'failed')
