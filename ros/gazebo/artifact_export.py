"""Checkpoint diagnostic artifacts without copying credentials or raw inputs."""
from pathlib import Path
import json
import sys
from safe_evidence import checkpoint_evidence, read_evidence_text, write_checkpoint
from public_report import report, failed_household_result

PUBLIC_FILES = (
    'result.json', 'error.json', 'hazard-progress.json', 'hazard-diagnostics.json', 'verification-report.json', 'transport-isolation.json',
    'compound-faults.json', 'attack-result.json', 'principal-isolation.json',
    'role-permissions.json', 'source-restart.json', 'sensor-faults.json', 'sensor-person.json',
    'source_world.log', 'source_vla.log', 'scenario_vla.log', 'setup.log', 'gazebo.log',
    'gazebo_gui.log', 'gate.log', 'relay.log', 'controller-permits.json', 'controller-timing.json',
    'controller_attacker.log', 'controller_reset.log', 'clock_bridge.log', 'robot_state_publisher.log',
    'sillok.jsonl', 'sealed-snapshot.jsonl', 'reference_bot.urdf',
)


def export_artifacts(root, output):
    if output is None:
        return
    root, output = Path(root).absolute(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = [Path(name) for name in PUBLIC_FILES]
    names += [Path('arm-' + case) / name for case in ('kill', 'stall', 'delay')
              for name in ('result.json', 'gate.log', 'sillok.jsonl', 'preparation-diagnostics.json')]
    rejected = []
    for name in names:
        source = root / name
        try:
            checkpoint_evidence(root, source, output / name)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            rejected.append((name, exc))
    if rejected:
        raise ValueError('Artifact export rejected: ' + ', '.join(str(name) for name, _ in rejected)) from rejected[0][1]


def final_export(root, output, prior_failure=False):
    """Preserve the primary failure while still attempting bounded diagnostics."""
    try:
        export_artifacts(root, output)
    except (OSError, ValueError) as exc:
        # A final collection failure must not leave a previously published
        # passing report. Use only bounded public metadata for its identity.
        if output is not None:
            try:
                output = Path(output).resolve()
                try:
                    previous = json.loads(read_evidence_text(output, 'verification-report.json'))
                except (OSError, ValueError):
                    previous = {}
                previous = previous if isinstance(previous, dict) else {}
                try:
                    result = json.loads(read_evidence_text(output, 'result.json'))
                except (OSError, ValueError):
                    result = {}
                result = result if isinstance(result, dict) else {}
                household = (previous.get('scope') in ('household_hazard_preflight_simulation', 'household_hazard_mandatory_gate_simulation')
                             or result.get('profile') == 'household_hazards')
                partial = (result if result.get('profile') == 'household_hazards' else
                           failed_household_result(output)) if household else None
                failed = report(partial, revision=previous.get('source_revision'),
                                run_id=previous.get('run_id'), failed=True)
                write_checkpoint(output / 'verification-report.json',
                                 json.dumps(failed, ensure_ascii=False).encode())
            except (OSError, ValueError) as publication_error:
                print('Failed-report publication rejected: ' + str(publication_error), file=sys.stderr)
        if not prior_failure:
            raise
        print('Final artifact export rejected: ' + str(exc), file=sys.stderr)
        return False
    return True
