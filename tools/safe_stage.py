"""Validate an explicit simulator staging directory before rsync --delete."""
from pathlib import Path
import sys

MARKER = '.haetae-sim-stage'


def prepare_stage(value, repo):
    requested = Path(value).absolute()
    target, repo = requested.resolve(), Path(repo).resolve()
    home = Path.home().resolve()
    if (requested.is_symlink() or target in (Path('/'), home) or
            target == repo or target in repo.parents or repo in target.parents):
        raise ValueError('stage must be outside the source tree, home and filesystem root')
    if target.exists() and (not target.is_dir() or
            (any(target.iterdir()) and not (target / MARKER).is_file())):
        raise ValueError('stage must be empty or contain the Haetae staging marker')
    target.mkdir(parents=True, exist_ok=True)
    (target / MARKER).write_text('Haetae simulator staging directory\n')
    return target


if __name__ == '__main__':
    print(prepare_stage(sys.argv[1], Path(__file__).resolve().parents[1]))
