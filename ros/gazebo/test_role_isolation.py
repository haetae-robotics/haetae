"""Root must not read role-mutable signer configuration on restart."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from role_isolation import Roles


class SourceParamsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.roles = Roles.__new__(Roles)
        self.roles.directories = {"vla": self.directory}
        self.roles.arm_joints = ["joint1"]
        self.roles._source_ttls = {}

    def test_cached_lease_uses_root_record_even_if_file_is_replaced(self):
        with mock.patch("role_isolation.os.fchown"):
            path = self.roles.source_params("vla", 1000)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.unlink()
        # Real principal entry names are root-pinned. Even under this stronger
        # replacement simulation the cached root call must not open the FIFO.
        os.mkfifo(path)
        with mock.patch.object(Path, "open", side_effect=AssertionError("root read role file")):
            self.assertEqual(self.roles.source_params("vla", 1000), path)
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                self.roles.source_params("vla", 0)

    def test_first_provision_refuses_existing_link_or_fifo(self):
        path = self.directory / "params.yaml"
        for kind in ("link", "fifo"):
            with self.subTest(kind=kind):
                if kind == "link":
                    path.symlink_to(self.directory / "missing")
                else:
                    os.mkfifo(path)
                with self.assertRaises(FileExistsError):
                    self.roles.source_params("vla", 1000)
                self.assertEqual(self.roles._source_ttls, {})
                path.unlink()
