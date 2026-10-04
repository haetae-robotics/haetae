import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from safe_evidence import read_evidence, checkpoint_evidence, write_checkpoint
from artifact_export import export_artifacts


class EvidenceBoundaryTest(unittest.TestCase):
    def test_regular_append_snapshot_and_atomic_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'sillok.jsonl', root / 'published'
            source.write_bytes(b'first\n')
            original_read = os.read
            def append_then_read(fd, count):
                with source.open('ab') as writer:
                    writer.write(b'later\n')
                return original_read(fd, count)
            with patch('safe_evidence.os.read', side_effect=append_then_read):
                self.assertEqual(read_evidence(root, source), b'first\n')
            checkpoint_evidence(root, source, target)
            self.assertEqual(target.read_bytes(), b'first\nlater\n')
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            source.write_bytes(b'')
            self.assertEqual(read_evidence(root, source), b'')

    def test_link_ancestry_special_files_owner_and_size_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary = root / 'ordinary'
            ordinary.write_bytes(b'fake unrelated data')
            link = root / 'link'
            link.symlink_to(ordinary)
            dangling = root / 'dangling'
            dangling.symlink_to(root / 'absent')
            parent = root / 'parent'
            parent.symlink_to(root, target_is_directory=True)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            hard = root / 'hard'
            os.link(ordinary, hard)
            for path in (link, dangling, parent / 'ordinary', fifo, root, hard):
                with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                    read_evidence(root, path)
            hard.unlink()
            with self.assertRaises(ValueError):
                read_evidence(root, ordinary, expected_uid=os.geteuid() + 1)
            with self.assertRaises(ValueError):
                read_evidence(root, ordinary, max_bytes=1)
            with self.assertRaises(ValueError):
                read_evidence(root, '../ordinary')

    def test_rejected_export_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / 'root', Path(directory) / 'out'
            root.mkdir()
            output.mkdir()
            (output / 'sillok.jsonl').write_text('previous diagnostic')
            unrelated = Path(directory) / 'fake.key'
            unrelated.write_text('FAKE_TEST_DATA')
            (root / 'sillok.jsonl').symlink_to(unrelated)
            with self.assertRaises(OSError):
                export_artifacts(root, output)
            self.assertEqual((output / 'sillok.jsonl').read_text(), 'previous diagnostic')
            self.assertEqual([p.name for p in output.iterdir()], ['sillok.jsonl'])

    def test_write_failure_removes_temporary_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('safe_evidence.os.fchmod', side_effect=OSError('synthetic write failure')):
                with self.assertRaises(OSError):
                    write_checkpoint(root / 'result.json', b'public synthetic result')
            self.assertEqual(list(root.iterdir()), [])
