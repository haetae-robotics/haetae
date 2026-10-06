"""Read diagnostic files beneath a trusted run root without following links."""

import os
from pathlib import Path
import stat
import tempfile
import time

# Long live sessions can produce substantial logs. Reject rather than truncate.
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024


def read_evidence(root, path, expected_uid=None, max_bytes=MAX_EVIDENCE_BYTES):
    root, path = Path(root), Path(path)
    relative = path.relative_to(root) if path.is_absolute() else path
    if not relative.parts or any(part in ('..', '.') for part in relative.parts):
        raise ValueError('evidence path must be beneath the trusted root')
    owner = os.geteuid() if expected_uid is None else expected_uid
    # Only the caller-owned anchor is canonicalized (e.g. macOS /var -> /private/var).
    directory = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    leaf = None
    try:
        for part in relative.parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=directory)
            os.close(directory)
            directory = next_directory
        # A counter writer atomically replaces its file. If that happens
        # between open and fstat, retry from the same pinned directory rather
        # than rejecting an ordinary rotation. Never retry unsafe live files.
        for attempt in range(8):
            leaf = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                           dir_fd=directory)
            info = os.fstat(leaf)
            if info.st_nlink != 0 or attempt == 7:
                break
            os.close(leaf)
            leaf = None
            time.sleep(0.001)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or
                info.st_nlink != 1 or not 0 <= info.st_size <= max_bytes):
            raise ValueError('unsafe evidence file type, owner, links or size: '
                             f'mode={info.st_mode:o} uid={info.st_uid} '
                             f'links={info.st_nlink} size={info.st_size}')
        chunks, remaining = [], info.st_size
        # Snapshot the initial length: a writer cannot keep this read alive by appending.
        while remaining:
            chunk = os.read(leaf, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)
    finally:
        if leaf is not None:
            os.close(leaf)
        os.close(directory)


def read_evidence_text(root, path, expected_uid=None):
    return read_evidence(root, path, expected_uid).decode('utf-8')


def write_checkpoint(target, content, *, mode=0o644):
    """Publish diagnostic bytes atomically, with ordinary host-readable permissions."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            checkpoint = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fchmod(temporary.fileno(), mode)
        os.replace(checkpoint, target)
    finally:
        if checkpoint is not None:
            checkpoint.unlink(missing_ok=True)


def checkpoint_evidence(root, source, target, expected_uid=None):
    write_checkpoint(target, read_evidence(root, source, expected_uid))
