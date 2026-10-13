"""Validate an explicit simulator staging directory before rsync --delete."""
from pathlib import Path
import os
import stat
import sys

MARKER = '.haetae-sim-stage'
MARKER_CONTENT = b'Haetae simulator staging directory\n'


def require_marker(marker):
    try:
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('stage marker must be a regular file, not a link') from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.getuid() or info.st_size != len(MARKER_CONTENT) or
                os.read(descriptor, len(MARKER_CONTENT) + 1) != MARKER_CONTENT):
            raise ValueError('stage marker must be an intact, owned, single-link file')
    finally:
        os.close(descriptor)


def prepare_stage(value, repo):
    requested = Path(value).absolute()
    target, repo = requested.resolve(), Path(repo).resolve()
    home = Path.home().resolve()
    if (requested.is_symlink() or target in (Path('/'), home) or
            target == repo or target in repo.parents or repo in target.parents):
        raise ValueError('stage must be outside the source tree, home and filesystem root')
    if target.exists():
        if not target.is_dir():
            raise ValueError('stage must be a directory')
        if any(target.iterdir()):
            require_marker(target / MARKER)
            return target
    target.mkdir(parents=True, exist_ok=True)
    try:
        with (target / MARKER).open('xb') as destination:
            destination.write(MARKER_CONTENT)
    except FileExistsError:
        require_marker(target / MARKER)
    return target


if __name__ == '__main__':
    print(prepare_stage(sys.argv[1], Path(__file__).resolve().parents[1]))
