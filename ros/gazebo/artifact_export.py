"""Checkpoint diagnostic artifacts without copying credentials or raw inputs."""
import os
from pathlib import Path
import shutil
import tempfile

PUBLIC_FILES = (
    'result.json', 'error.json', 'verification-report.json', 'transport-isolation.json',
    'compound-faults.json', 'attack-result.json', 'principal-isolation.json',
    'role-permissions.json', 'source-restart.json', 'sensor-faults.json', 'sensor-person.json',
    'source_world.log', 'source_vla.log', 'scenario_vla.log', 'setup.log', 'gazebo.log',
    'gazebo_gui.log', 'gate.log', 'clock_bridge.log', 'robot_state_publisher.log',
    'sillok.jsonl', 'sealed-snapshot.jsonl', 'reference_bot.urdf',
)


def export_artifacts(root, output):
    if output is None:
        return
    root, output = Path(root), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = [Path(name) for name in PUBLIC_FILES]
    names += [Path('arm-' + case) / name for case in ('kill', 'stall', 'delay')
              for name in ('result.json', 'gate.log', 'sillok.jsonl')]
    for name in names:
        source = root / name
        if not source.is_file():
            continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            checkpoint = Path(temporary.name)
        try:
            shutil.copy2(source, checkpoint)
            os.replace(checkpoint, target)
        finally:
            checkpoint.unlink(missing_ok=True)
