"""Checkpoint diagnostic artifacts without copying credentials or raw inputs."""
from pathlib import Path
import sys
from safe_evidence import checkpoint_evidence

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
    root, output = Path(root).absolute(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = [Path(name) for name in PUBLIC_FILES]
    names += [Path('arm-' + case) / name for case in ('kill', 'stall', 'delay')
              for name in ('result.json', 'gate.log', 'sillok.jsonl')]
    for name in names:
        source = root / name
        try:
            checkpoint_evidence(root, source, output / name)
        except FileNotFoundError:
            continue


def final_export(root, output, prior_failure=False):
    """Preserve the primary failure while still attempting bounded diagnostics."""
    try:
        export_artifacts(root, output)
    except (OSError, ValueError) as exc:
        if not prior_failure:
            raise
        print('Final artifact export rejected: ' + str(exc), file=sys.stderr)
        return False
    return True
