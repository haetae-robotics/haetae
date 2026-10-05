"""Stop only an owned child, retaining its PID until group signalling finishes."""
import os
import signal
import subprocess


def stop_process(child, session=False):
    if child is None:
        return
    # poll()/wait() can reap the leader and let its PID/PGID be reused. Never
    # signal a group after that; an unreaped leader still reserves its PID.
    if child.returncode is None:
        if session:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # No live group; still reap the owned leader below.
            except PermissionError as exc:
                # Darwin returns EPERM for an empty session with an exited,
                # unreaped leader. Accept only a completed owned child; a
                # live leader's permission failure must still abort cleanup.
                try:
                    child.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    raise exc
                return  # Never retry killpg after releasing this PID.
        else:
            child.kill()  # Popen handles its own child/reaping race.
    child.wait(timeout=2)


def stop_pair(authorizer, relay):
    try:
        stop_process(authorizer, session=True)  # Only start_new_session=True children.
    finally:
        stop_process(relay)
