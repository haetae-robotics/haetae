import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

from permit_keys import deployment, private_bytes, provision
from permit_protocol import decode_json, frame, payload, bind_payload


class PermitContractTest(unittest.TestCase):
    def setUp(self):
        self.value = dict(install="01"*16, epoch=1, generation=1, sequence=0, challenge=1, issued=0,
                          nonce="02"*16, op="ARM")

    def test_fixed_binary_encoding_and_action_binding(self):
        encoded = payload(self.value)
        self.assertEqual(len(encoded), 76)
        self.assertEqual(encoded[:16], b"HAETAE-LED-H2\0\0\0")
        self.assertNotEqual(encoded, payload(dict(self.value, op="RUN")))
        self.assertNotEqual(encoded, payload(dict(self.value, epoch=2)))
        self.assertNotEqual(encoded, payload(dict(self.value, nonce="03"*16)))

    def test_strict_request_contract(self):
        for name, bad in [("epoch", True), ("sequence", -1), ("challenge", 0),
                          ("issued", 2**32), ("op", "MOTOR"), ("nonce", "GG"*16)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload(dict(self.value, **{name: bad}))
        with self.assertRaises(ValueError):
            payload(dict(self.value, decision=True))
        with self.assertRaises(ValueError):
            decode_json(b'{"epoch":1,"epoch":2}')
        with self.assertRaises(ValueError):
            decode_json(b" "*513)
        with self.assertRaises(ValueError):
            frame(self.value, "00"*63)
        bind = dict(self.value, op="BIND")
        self.assertEqual(len(bind_payload(bind, "03"*32, "04"*32)), 140)
        with self.assertRaises(ValueError):
            bind_payload(self.value, "03"*32, "04"*32)
        with self.assertRaises(ValueError):
            frame(bind, "00"*64, 200, "03"*32)

    def test_explicit_private_provisioning_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device"
            config = provision(path)
            self.assertEqual(deployment(path / "deployment.json"), config)
            self.assertEqual(len(private_bytes(path / "authorizer.seed")), 32)
            with self.assertRaises(FileExistsError):
                provision(path)
            (path / "authorizer.seed").chmod(0o644)
            with self.assertRaises(ValueError):
                private_bytes(path / "authorizer.seed")
            (path / "authorizer.seed").chmod(0o600)
            os.symlink(path / "authorizer.seed", path / "linked.seed")
            with self.assertRaises(OSError):
                private_bytes(path / "linked.seed")

    def test_authorizer_ignores_relay_decisions_and_latches(self):
        from permit_authorizer import Authorizer
        class Gate:
            def cycle(self, fault):
                return False
        authorizer = Authorizer.__new__(Authorizer)
        authorizer.config = {"install": self.value["install"]}
        authorizer.gate = Gate()
        authorizer.scenario, authorizer.seconds = "person", 1
        authorizer.started = None
        authorizer.binding = 1, 1
        authorizer.sequence = 0
        authorizer.challenge = 0
        authorizer.locked = False
        authorizer.last_fault = None
        authorizer.armed_authorized = False
        self.assertEqual(authorizer.approve(self.value)["kind"], "terminal")
        self.assertTrue(authorizer.locked)
        self.assertEqual(authorizer.approve(dict(self.value, generation=2))["kind"], "terminal")
        class ExpiredGate:
            def cycle(self, fault):
                return True
            def remaining(self):
                return 0
        authorizer.gate = ExpiredGate()
        authorizer.locked = False
        authorizer.session_key = bytes(32)
        self.assertEqual(authorizer.approve(self.value), {"kind": "terminal", "reason": "gate_expired"})
        self.assertTrue(authorizer.locked)

    def test_optimized_usb_qualification_rejects_exposed_dfu_before_port_open(self):
        # This subprocess really uses -O: a normal unit test cannot detect
        # qualification oracles that disappear under Python optimization.
        script = textwrap.dedent('''
            import hashlib, json, tempfile
            from pathlib import Path
            from unittest.mock import patch
            import permit_usb_verify as usb
            from permit_keys import provision
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = provision(root / 'device')
                usb.ROOT, usb.OUT = root, root / 'evidence'
                (root / 'target/release').mkdir(parents=True)
                (root / 'target/release/haetae').write_bytes(b'gate')
                build = root / 'artifacts/controller-permit-build'
                build.mkdir(parents=True)
                (build / 'manifest.json').write_text(json.dumps(dict(config,
                    resolved_core='haetae_permit', firmware_source_sha256={})))
                with patch.object(usb, 'interfaces', return_value=[2, 10, 254]), \\
                     patch.object(usb.subprocess, 'check_output', side_effect=['test-head\\n', '']), \\
                     patch.object(usb, 'PermitLink', side_effect=RuntimeError('unexpected USB access')) as link:
                    try:
                        usb.main('must-not-open', root / 'device')
                    except AssertionError as exc:
                        if 'DFU still exposed' not in str(exc):
                            raise
                    else:
                        raise RuntimeError('exposed DFU was accepted under -O')
                    link.assert_not_called()
                report = json.loads((usb.OUT / 'report.json').read_text())
                if report['passed'] is not False or report['cases'] != []:
                    raise RuntimeError('failed qualification wrote successful evidence')
        ''')
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent))
        result = subprocess.run([sys.executable, "-O", "-c", script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_run_provenance_rejects_stale_or_failed_evidence(self):
        from permit_provenance import require_verified_h2
        digest = lambda value: hashlib.sha256(value).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('artifacts/controller-permit', 'artifacts/controller-permit-usb',
                           'artifacts/controller-permit-build/build', 'target/release',
                           'hardware/uno_r4_permit', 'device'):
                (root / folder).mkdir(parents=True)
            binary = root / 'target/release/haetae'
            binary.write_bytes(b'gate')
            image = root / 'artifacts/controller-permit-build/build/uno_r4_permit.ino.bin'
            image.write_bytes(b'private image fixture')
            source = root / 'hardware/uno_r4_permit/guard.h'
            source.write_bytes(b'guard')
            sources = {'hardware/uno_r4_permit/guard.h': digest(b'guard')}
            config = dict(install='01'*16, public_key='02'*32, controller_public_key='03'*32)
            (root / 'device/deployment.json').write_text(json.dumps(config))
            manifest = dict(config, firmware_source_sha256=sources, resolved_core='haetae_permit',
                            binary_sha256=digest(b'private image fixture'))
            software = dict(passed=True, source_revision='head', source_dirty=False,
                            firmware_sha256=sources, gate_sha256=digest(b'gate'))
            usb = dict(passed=True, source_revision='head', source_dirty=False,
                       firmware_source_sha256=sources, gate_sha256=digest(b'gate'),
                       build_manifest={k: v for k, v in manifest.items() if k not in ('install', 'public_key')})
            files = [('artifacts/controller-permit-build/manifest.json', manifest),
                     ('artifacts/controller-permit/report.json', software),
                     ('artifacts/controller-permit-usb/report.json', usb)]
            for path, value in files:
                (root / path).write_text(json.dumps(value))
            def check():
                with patch('permit_provenance.subprocess.check_output', side_effect=['head\n', '']):
                    return require_verified_h2(root, binary, root / 'device')
            self.assertEqual(check(), (software, usb))
            usb['passed'] = False
            (root / files[2][0]).write_text(json.dumps(usb))
            with self.assertRaises(RuntimeError):
                check()

            usb['passed'] = True
            (root / files[2][0]).write_text(json.dumps(usb))
            binary.write_bytes(b'changed gate')
            with self.assertRaises(RuntimeError):
                check()
            binary.write_bytes(b'gate')
            source.write_bytes(b'changed source')
            with self.assertRaises(RuntimeError):
                check()
            source.write_bytes(b'guard')
            image.write_bytes(b'changed image')
            with self.assertRaises(RuntimeError):
                check()

    def test_isolation_stages_only_public_runtime_without_opening_checkout(self):
        from permit_isolation_verify import stage_public_runtime
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'private-checkout'
            root.mkdir(mode=0o700)
            for folder in ('tools', 'ros/haetae_gate'):
                (root / folder).mkdir(parents=True)
                (root / folder / 'module.py').write_text('pass\n')
                (root / folder / 'private.seed').write_bytes(b'excluded private fixture')
            (root / 'haetae-permit').write_text('entry')
            binary = root / 'gate'
            binary.write_bytes(b'gate binary')
            dest = Path(tmp) / 'public-runtime'
            entry, gate = stage_public_runtime(root, binary, dest)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(entry.stat().st_mode & 0o777, 0o555)
            self.assertEqual(gate.read_bytes(), binary.read_bytes())
            self.assertFalse(list(dest.rglob('*.seed')))
            self.assertEqual((dest / 'tools/module.py').stat().st_mode & 0o777, 0o444)

    def test_cleanup_never_signals_a_reaped_session_id(self):
        from permit_process import stop_process
        child = Mock(pid=12345, returncode=None)
        def reap(**kwargs):
            child.returncode = 0
        child.wait.side_effect = reap
        def signal_owned_group(pid, sig):
            if child.returncode is not None:
                raise RuntimeError('attempted signal of a reusable PGID')
            self.assertEqual(pid, child.pid)
        with patch('permit_process.os.killpg', side_effect=signal_owned_group) as killpg:
            stop_process(child, session=True)
            killpg.assert_called_once()
        # A leader already reaped during startup/fault handling cannot be
        # used to identify a process group, even if its numeric PID is known.
        with patch('permit_process.os.killpg') as killpg:
            stop_process(child, session=True)
            killpg.assert_not_called()



if __name__ == "__main__":
    unittest.main()
