"""Actual Gazebo controller admission under a compromised relay UID.

Independent root test fixtures own a real Rust enforcer and permit signer.
Synthetic clear-world semantics remain trusted. No test signer runs in relay.
Shared-runner delay can make one case attempt inconclusive (permit_attempts);
the case is retried from a new maintenance reset and never counts as a pass.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

from geometry_msgs.msg import TwistStamped
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ros/haetae_gate"))
sys.path.insert(0, str(REPO / "ros/haetae_sim"))
from bridge import Bridge, BridgeFailure, ExpiredActuation, StaleActuation, require_fresh_actuation
from controller_permits import (PermitSigner, IDLE, MAX_LEASE_NS, SIM_ORDERING_BACKDATE_NS,
                                base_digest, arm_digest)
from signing import Signer
from run_scenario import fixture
from product_model import ARM_JOINTS, arm_policy
from role_isolation import fresh_fixture, UIDS
from permit_attempts import (PENDING, TIMING_FILE, VERIFIER_WINDOW_NS, AttemptLog, GrantChain, RunnerDelay,
                             bounded_attempts, check_timely, fail_closed_settled, goal_counted, late_stop_verdict,
                             motion_after, motion_checked, negative_timing, permit_age_ns, permit_fields,
                             precondition_miss, refusal, refusal_hold, refused_motion, require_fresh_witness,
                             require_live, require_no_fail_closed, require_refused_inside_lease,
                             require_refused_inside_stale_window, require_stale_window, reset_lease_events)
from public_report import DRIFT_LIMIT, NEGATIVE_REASONS, RECOVERY_REASONS, negative_row_passed, report


def measured_world(world):
    # Pin each immutable ROS message once, keeping fields and their source
    # stamp together even if a callback replaces the latest message.
    odom, joint = world.odom, world.joint
    position = odom.pose.pose.position
    q = odom.pose.pose.orientation
    stamp = min(msg.header.stamp.sec * 1000 + msg.header.stamp.nanosec // 1_000_000
                for msg in (odom, joint))
    return {"stamp_ms": stamp, "robot": {
        # Same spawn-relative odom frame as GazeboWorld.pose().
        "pose": {"x": 5.0 + position.x, "y": 5.0 + position.y},
        "yaw": math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)),
        "twist": {"linear": odom.twist.twist.linear.x, "angular": odom.twist.twist.angular.z},
        "joints": [{"name": name, "position": joint.position[joint.name.index(name)],
                    "velocity": joint.velocity[joint.name.index(name)]} for name in ARM_JOINTS]},
        "humans": [], "confidence": 1.0}


def require_world_accepted(step, snapshot):
    updated = (step.get("outcome") or {}).get("world_updated")
    if (not isinstance(updated, dict) or type(updated.get("stamp_ms")) is not int
            or updated["stamp_ms"] != snapshot["stamp_ms"]):
        raise AssertionError("controller probe measured world was not accepted: " + json.dumps(step))


def exercise(world, root, binary, roles, processes, start, stop, command, wait_for, env):
    """Never invoked unless the ordinary authorization process is already gone."""
    if "gate" in processes:
        raise AssertionError("permit probes require no live authorizer")
    def stopped():
        joint = world.joint
        odom = world.odom
        age = time.monotonic()
        return (joint is not None and odom is not None and 0 <= age-world.joint_received < .1
                and 0 <= age-world.odom_received < .1
                and all(math.isfinite(value) and abs(value) < .03 for value in
                        (odom.twist.twist.linear.x, odom.twist.twist.angular.z))
                and all(j in joint.name and joint.name.index(j) < len(joint.velocity)
                        and math.isfinite(joint.velocity[joint.name.index(j)])
                        and abs(joint.velocity[joint.name.index(j)]) < .03 for j in ARM_JOINTS))
    wait_for(stopped, 2, processes, "fresh measured wheel and arm stop before relay replacement")
    relay = processes.pop("relay")
    stop(relay, force=True)
    attacker, log = start([sys.executable, str(REPO / "ros/gazebo/controller_attack.py")],
        root, "controller_attacker", processes, roles.environment("relay", env), UIDS["relay"], input_pipe=True)
    lab = root / "permit-fixture"
    lab.mkdir()
    source = None
    bridge = None
    fixture_id = 0
    signer = PermitSigner(roles.directories["gate"] / "controller.key",
                          sim_backdate_ns=SIM_ORDERING_BACKDATE_NS)
    proposal_id = 0
    packet_id = 0
    reset_id = 0
    resetter = None
    reset_log = None
    results = {}
    # CI non-blocking measurement and per-attempt record; never decides a verdict.
    timing = AttemptLog(root / TIMING_FILE)
    def now():
        return world.get_clock().now().nanoseconds // 1_000_000
    def send(packet):
        nonlocal packet_id
        packet_id += 1
        attacker.stdin.write((json.dumps({**packet, "id": packet_id}) + "\n").encode())
        attacker.stdin.flush()
    def require_timely(permit, stage):
        # Never deliver a permit the verifier's own predicate must already refuse.
        fields, wall_ns, sim_ns = permit_fields(permit), time.monotonic_ns(), now() * 1_000_000
        timing.sample("send_age_ms", permit_age_ns(fields, wall_ns, sim_ns) / 1e6)
        check_timely(stage, fields, wall_ns, sim_ns)
        return fields
    def send_timely(packet, permit, stage, chain=None):
        # A positive permit also needs a live lease to land on: the last one sent.
        # Which grant the controller admitted is judged later, from its own counter.
        fields = require_timely(permit, stage)
        if chain is not None:
            require_live(stage, chain.last(), time.monotonic_ns(), now() * 1_000_000)
            chain.sent(fields)
        send(packet)
    def telemetry(target):
        return world.base_guard_states if target == "base" else world.guard_states
    def guard(target):
        rows = telemetry(target)
        return rows[-1][1] if rows else {}
    def since(target, mark):
        return [row for _, row in telemetry(target)[mark:]]
    def wait_row(target, accept, timeout, description):
        # Keep the exact telemetry row that satisfied the wait; never re-read a newer one.
        found = []
        def predicate():
            row = guard(target)
            if accept(row):
                found.append(row)
                return True
            return False
        wait_for(predicate, timeout, processes, description)
        return found[0]
    def wait_judged(target, mark, judge, timeout, description):
        # judge(rows received since mark) returns None until it has a verdict; it
        # raises for a failure and for an inconclusive (runner-delayed) outcome.
        found = []
        def predicate():
            verdict = judge(since(target, mark))
            if verdict is not None:
                found.append(verdict)
            return verdict is not None
        wait_for(predicate, timeout, processes, description)
        return found[0]
    def fail_closed_check(target, chain, hold_after, stage):
        # Classify the first controller fail-closed event published after this point.
        # check() returns False while there is none or its hold evidence is pending.
        mark, checkpoint, rejected = len(telemetry(target)), time.monotonic_ns(), guard(target).get("rejected", 0)
        def check():
            check.pending = require_no_fail_closed(target, since(target, mark), checkpoint, rejected,
                                                   chain, hold_after, stage) is not None
            return False
        check.pending = False
        return check
    def settle(check, description):
        # Let a visible fail-closed event finish its classification; check() raises it.
        try:
            wait_for(lambda: check() or not check.pending, 1, processes, description)
        except TimeoutError:
            raise AssertionError(description + ": controller event without hold evidence") from None
    def positive_motion(target, chain, late, moved, timeout, description, action):
        # A RunnerDelay from the action (approval, budget, send checks) leaves only after
        # the controller's own fail-closed events were judged.
        with fail_closed_settled(lambda: settle(late, description)):
            wait_for(lambda: moved() or late(), timeout, processes, description, action=action)
        # Motion may cross its threshold while the last permit is still in flight. Wait until
        # the controller counted it (late() raises for its refusal or a lapse), then judge every
        # event published since the checkpoint: a non-timing refusal or premature lock still
        # fails, and a timing outcome is inconclusive, never a pass.
        wait_for(lambda: late() or not chain.in_flight(guard(target).get("accepted")), 1, processes,
                 description + ": last permit counted")
        # Exactly the permits this harness sent were admitted; a count past them fails
        # before any timing outcome is judged.
        chain.admitted(guard(target).get("accepted"), description)
        settle(late, description)
    def joint_history():
        # Measured arm positions stamped by the controller's own (simulation) update.
        return [(msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec,
                 [msg.position[list(msg.name).index(j)] for j in ARM_JOINTS])
                for _, msg in list(world.joint_measurements) if all(j in msg.name for j in ARM_JOINTS)]
    def require_counted(target, before, stage):
        # Arm goals travel on a reliable action whose ingress counts every goal, even one that
        # arrives after a lapse, so a valid arm goal that the controller does not count (admit
        # or refuse) within the 1 s admission wait fails. Base commands travel on a
        # best-effort topic and are not checked.
        if target != "arm":
            return
        try:
            wait_for(lambda: guard(target).get("accepted", 0) > before.get("accepted", 0)
                     or guard(target).get("rejected", 0) > before.get("rejected", 0),
                     1, processes, stage + ": valid arm goal counted")
        except TimeoutError:
            # Chained to the inconclusive outcome that triggered this check, for the CI log.
            raise AssertionError(stage + ": the controller never counted a valid arm goal")
    def arm_hold(stage, mark, sent_ns, rejected_before, hold_after):
        # The arm's counted refusal of a packet and its prompt hold, and its motion from the
        # update stamped on the first telemetry row that shows the refusal, or from the hold if
        # earlier (simulation clock, so receipt delay cannot hide it). As on the conclusive
        # path, the refusal gets 2 s to show before it counts as never counted.
        try:
            refused, held = wait_judged("arm", mark, lambda rows: refusal_hold(
                rows, sent_ns, rejected_before, hold_after, stage), 2, stage + " counted refusal and prompt hold")
        except TimeoutError:
            raise AssertionError(stage + ": no controller refusal and hold bound the arm's motion")
        start = min(refused["stamp_ms"], held["cutoff_ms"]) * 1_000_000
        moved = motion_after(joint_history(), start, world.arm_positions())
        if moved is None or moved > DRIFT_LIMIT:
            raise AssertionError(stage + " moved after the controller refused it: " + json.dumps(
                {"drift_after_refusal": moved, "refusal_stamp_ms": refused["stamp_ms"],
                 "hold_cutoff_ms": held["cutoff_ms"]}))
        return {"refusal_stamp_ms": refused["stamp_ms"], "hold_cutoff_ms": held["cutoff_ms"],
                "drift_after_refusal": moved}
    def feed(role, payload):
        envelope = source.sign(role, payload)
        try:
            return bridge.request({"k": "signed", "t": now(), "data": json.dumps(envelope)})
        except BridgeFailure as exc:
            # Only the fixture issuer's 500 ms response bound (bridge.py:95,98) is
            # runner delay. Nothing was signed; the next attempt replaces the issuer.
            if str(exc) == "gate response timeout":
                raise RunnerDelay("fixture issuer", {"cause": "fixture_issuer_response_timeout"}) from exc
            raise
    def world_snapshot():
        # Provisioning a fresh Rust fixture is synchronous. Wait for actual
        # fresh feedback afterwards; never relabel an old observation as fresh.
        wait_for(lambda: 0 <= time.monotonic()-world.joint_received < .05 and
                 0 <= time.monotonic()-world.odom_received < .05,
                 2, processes, "fresh measured controller probe world")
        return measured_world(world)
    def budget(step, sim):
        status = step["status"]
        remaining = min(200, 200 - status["world_age_ms"])
        if status.get("active_expires_ms") is not None:
            remaining = min(remaining, status["active_expires_ms"] - sim // 1_000_000)
        if remaining <= 0:
            raise RunnerDelay("rust budget", {"cause": "rust_authority_expired_before_signing",
                                              "remaining_ms": remaining})
        return remaining * 1_000_000
    def approve(action):
        nonlocal proposal_id
        world_wall, world_sim = time.monotonic_ns(), now()
        snapshot = world_snapshot()
        world_step = feed("world", snapshot)
        require_world_accepted(world_step, snapshot)
        # Same origin -> fresh feedback -> Rust-accepted world steps as renew().
        timing.sample("world_admission_ms", max((time.monotonic_ns()-world_wall)/1e6, now()-world_sim))
        proposal_id += 1
        begin = time.monotonic_ns()
        stamp = now()
        step = feed("vla", {"id": proposal_id, "source": "vla", "timestamp_ms": stamp, "action": action})
        elapsed = max((time.monotonic_ns()-begin)/1e6, now()-stamp)
        timing.sample("actuation_admission_ms", elapsed)
        try:
            require_fresh_actuation(step, elapsed, now(), 200, 50)
        except StaleActuation as exc:
            # A late or expired Rust response is never signed: runner delay. A malformed
            # response (missing expiry/world age) still fails, however late it arrived.
            status = step.get("status") or {}
            well_formed = type(status.get("active_expires_ms")) is int and type(status.get("world_age_ms")) is int
            if well_formed and (elapsed >= 50 or isinstance(exc, ExpiredActuation)):
                raise RunnerDelay("rust approval", {"cause": "stale_or_expired_rust_approval",
                                                    "elapsed_ms": elapsed, "error": str(exc)}) from exc
            raise
        if action["type"] == "velocity" and step["cmd"]["linear"] != action["linear"]:
            raise AssertionError("Rust base positive control denied")
        if action["type"] == "joint_trajectory" and not isinstance(step.get("arm"), dict):
            raise AssertionError("Rust arm positive control denied: " + json.dumps(step))
        return stamp * 1_000_000, begin, step
    def token(target, kind, digest, sim, wall, remaining=200_000_000):
        return signer.sign(target, kind, digest, sim, remaining, wall)
    def stale_permit(target, kind, digest, stage):
        # Deliberately stale and signed at the instant it leaves: both signed origins
        # are 60 ms old (the signer backdates the simulation origin once more) and the
        # lease is the full 200 ms, so its own ends are about 140 ms ahead and only
        # the verifier's 50 ms age bound can refuse it.
        sim, wall = now() * 1_000_000, time.monotonic_ns()
        permit = token(target, kind, digest, sim + SIM_ORDERING_BACKDATE_NS - 60_000_000,
                       wall - 60_000_000, MAX_LEASE_NS)
        fields = permit_fields(permit)
        require_stale_window(stage, fields, time.monotonic_ns(), now() * 1_000_000)
        return permit, fields
    def base_packet(kind="command", speed=.15):
        sim, wall, step = approve({"type": "velocity", "linear": speed, "angular": 0.0, "ttl_ms": 200})
        msg = TwistStamped()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(sim, 1_000_000_000)
        msg.twist.linear.x = float(step["cmd"]["linear"])
        return {"base": {"stamp": sim, "linear": msg.twist.linear.x, "angular": 0.0,
            "permit": token("base", kind, base_digest(msg), sim, wall, budget(step, sim))}}
    def arm_packet():
        # The controller refuses a goal whose signed (backdated) simulation origin
        # precedes its last hold cutoff (permit.hpp:91); never sign one that early.
        cutoff = guard("arm").get("cutoff_ms")
        if type(cutoff) is int:
            wait_for(lambda: now() - SIM_ORDERING_BACKDATE_NS // 1_000_000 >= cutoff, 1, processes,
                     "arm goal origin at or after the controller hold cutoff")
        positions = world.arm_positions()
        end = [positions[0] + (-.3 if positions[0] > .2 else .3), *positions[1:]]
        sim, wall, step = approve({"type": "joint_trajectory", "ttl_ms": 1000,
            "points": [{"time_from_start_ms": 0, "positions": positions},
                       {"time_from_start_ms": 800, "positions": end},
                       {"time_from_start_ms": 1000, "positions": end}]})
        trajectory = JointTrajectory()
        trajectory.joint_names = list(ARM_JOINTS)
        # Start at controller admission; signing clocks remain the original
        # Rust approval clocks and are not encoded as a past JTC start time.
        for point in step["arm"]["execute"]["points"]:
            p = JointTrajectoryPoint(); p.positions = [float(v) for v in point["positions"]]
            p.time_from_start.sec, p.time_from_start.nanosec = divmod(point["time_from_start_ms"]*1_000_000, 1_000_000_000)
            trajectory.points.append(p)
        digest = arm_digest(trajectory)
        return {"arm": {"joints": ARM_JOINTS, "stamp": 0,
            "points": [{"positions": list(p.positions), "time": p.time_from_start.sec*1_000_000_000+p.time_from_start.nanosec}
                       for p in trajectory.points], "permit": token("arm", "goal", digest, sim, wall, budget(step, sim))}}, digest
    def new_issuer():
        nonlocal bridge, source, fixture_id, proposal_id
        if bridge is not None:
            bridge.close()
        fixture_id += 1
        session = lab / str(fixture_id)
        session.mkdir()
        fixture(session, binary, arm=True, arm_policy=arm_policy())
        public, _ = fresh_fixture(session)
        source = Signer(session / "trust.json", session / "state.json",
                       {role: str(session / (role + ".key")) for role in ("world", "fault", "vla")})
        bridge = Bridge([binary, "enforce", "--stdio", "--policy", str(session / "policy.json"),
            "--state", str(session / "state.json"), "--sillok", str(session / "sillok.jsonl"),
            "--key", str(session / "log.key"), "--trust", str(session / "trust.json"), "--root-pubkey", public])
        proposal_id = 0
    def reset():
        nonlocal reset_id
        wait_for(stopped, 2, processes, "fresh measured wheel and arm stop before maintenance reset")
        new_issuer()
        previous = {target: guard(target).get("nonce") for target in ("base", "arm")}
        reset_id += 1
        resetter.stdin.write((json.dumps(reset_id) + "\n").encode())
        resetter.stdin.flush()
        marker = json.dumps({"reset_complete": reset_id})
        wait_for(lambda: marker in (root / "controller_reset.log").read_text(),
                 15, processes, "bounded trusted controller maintenance reset")
        with (root / "setup.log").open("a") as audit:
            audit.write("$ trusted controller maintenance reset " + str(reset_id) + "\n" + marker + "\n")
        wait_for(lambda: all(guard(target).get("nonce") and
                 guard(target).get("nonce") != previous[target] for target in ("base", "arm")),
                 2, processes, "fresh activation nonce telemetry")
        for target in ("base", "arm"):
            signer.observe(target, guard(target)); signer.sequence[target] = 0
        sim, wall, step = approve({"type": "stop"})
        msg = TwistStamped(); msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(sim, 1_000_000_000)
        packet = {"base": {"stamp": sim, "linear": 0.0, "angular": 0.0,
                       "permit": token("base", "reset", base_digest(msg), sim, wall, budget(step, sim))},
              "heartbeat": token("arm", "reset", IDLE, sim, wall, budget(step, sim))}
        grants = {"base": permit_fields(packet["base"]["permit"]), "arm": permit_fields(packet["heartbeat"])}
        # Both controllers hold since activation, so any hold after this point is new.
        unlocked = time.monotonic_ns()
        late = [fail_closed_check(target, GrantChain(grants[target], guard(target).get("accepted", 0)),
                                  unlocked, target + " maintenance reset") for target in ("base", "arm")]
        send_timely(packet, packet["heartbeat"], "maintenance reset")
        wait_for(lambda: any(check() for check in late) or
                 (not guard("base").get("holding", True) and not guard("arm").get("holding", True)),
            2, processes, "explicit signed controller reset")
        return {target: {"nonce": signer.challenges[target], "fields": grants[target],
                         "wall_end_ns": grants[target]["wall_end_ns"],
                         "sim_end_ns": grants[target]["sim_end_ns"],
                         # Counters after the admitted reset; only time may change before the negative packet.
                         "accepted": guard(target).get("accepted", 0),
                         "rejected": guard(target).get("rejected", 0),
                         # This row showed the reset unlocked; any later hold is new.
                         "unlocked_wall_ns": guard(target).get("published_wall_ns", 0)}
                for target in ("base", "arm")}
    def recover(target, stage):
        # A new correctly signed command cannot clear the local lock.
        # A fresh independent Rust issuer may approve a new command,
        # but sends no controller reset. This tests the local latch
        # with a fresh signature/time, not just another stale replay.
        entry, latched = len(telemetry(target)), guard(target)
        def still_latched():
            # Nothing reaches the controller before the recovery packet leaves, so it must keep
            # holding with the same nonce and counters; a harness delay is inconclusive only then.
            for row in [latched, *since(target, entry)]:
                if row.get("holding") is not True or any(row.get(key) != latched.get(key)
                                                         for key in ("nonce", "accepted", "rejected")):
                    raise AssertionError("automatic recovery before the " + stage + " recovery packet left: "
                                         + json.dumps({"nonce_matches": row.get("nonce") == latched.get("nonce"),
                                                       **{key: row.get(key) for key in
                                                          ("holding", "accepted", "rejected", "reason")}},
                                                      sort_keys=True))
        with fail_closed_settled(still_latched):
            new_issuer()
            approve({"type":"stop"})
            fresh=base_packet() if target=="base" else arm_packet()[0]
            mark, before = len(telemetry(target)), guard(target)
            witness = {"nonce_before": before.get("nonce"), "accepted_before": before.get("accepted", 0),
                       "rejected_before": before.get("rejected", 0)}
            recovery_pose, recovery_joints = world.pose(), world.arm_positions()
            if target == "base":
                # Only a fresh command reaches the base latch check (freshness precedes it).
                require_timely(fresh["base"]["permit"], stage + " recovery")
        def check_motion():
            until=time.monotonic()+.3
            wait_for(lambda:time.monotonic()>=until,2,processes,"recovery remains locked")
            drift = (math.dist(world.pose(), recovery_pose) if target == "base" else
                max(abs(a-b) for a,b in zip(recovery_joints, world.arm_positions())))
            if guard(target).get("holding") is not True or drift > DRIFT_LIMIT:
                raise AssertionError("automatic recovery")
            return drift
        witness["sent_wall_ns"] = time.monotonic_ns()
        # The arm action ingress refuses a held controller before any permit check.
        send(fresh)
        with motion_checked(check_motion):
            refused, _ = wait_judged(target, mark, lambda rows: refusal(
                target, rows, witness, RECOVERY_REASONS[target], ("freshness",) if target == "base" else (),
                None, stage + " recovery"), 2, "fresh recovery rejected at locked controller")
        recovery_drift = check_motion()
        if guard(target).get("accepted", 0) != witness["accepted_before"]:
            raise AssertionError("automatic recovery")
        return {"recovery_did_not_rearm": True, "recovery_rejection_observed": True,
                "recovery_rejection_reason": refused["reason"], "recovery_drift": recovery_drift}
    def require_report_row(target, case, row):
        # The Gazebo job and the alpha package judge the same witness.
        if not negative_row_passed(target, case, row):
            raise AssertionError(target + " " + case + " witness does not satisfy the public report: "
                                 + json.dumps(row, sort_keys=True))
    def base_positive():
        grant = reset()["base"]
        chain = GrantChain(grant["fields"], grant["accepted"] - 1)
        before = world.pose()
        late = fail_closed_check("base", chain, grant["unlocked_wall_ns"], "signed base physically moves")
        def send_command():
            # One command in flight at a time, so a refusal is never hidden behind the next.
            if chain.in_flight(guard("base").get("accepted")):
                return
            packet = base_packet()
            send_timely(packet, packet["base"]["permit"], "base command", chain)
        positive_motion("base", chain, late, lambda: abs(world.pose()[0]-before[0]) > .03, 4,
                        "signed base physically moves", send_command)
        return {"ok": True, "moved_m": abs(world.pose()[0]-before[0])}
    def arm_positive():
        grant = reset()["arm"]
        chain = GrantChain(grant["fields"], grant["accepted"] - 1)
        initial = world.arm_positions()
        before = guard("arm")
        accepted = before.get("accepted", 0)
        late = fail_closed_check("arm", chain, grant["unlocked_wall_ns"], "signed arm physically moves")
        with fail_closed_settled(lambda: settle(late, "signed arm physically moves")):
            packet, digest = arm_packet()
            send_timely(packet, packet["arm"]["permit"], "arm goal", chain)
        # Renewal uses a later sequence on a different DDS route. Wait for
        # controller admission of the goal before any renewal can overtake it.
        # An inconclusive outcome still needs the controller to have counted the goal.
        with goal_counted(lambda: require_counted("arm", before, "signed arm physically moves")):
            wait_for(lambda: late() or (not late.pending and guard("arm").get("accepted", 0) > accepted and
                     not guard("arm").get("holding", True)),
                     1, processes, "trusted controller admits signed arm goal")
        def renew():
            # The world response already rechecks the same Rust goal. A second
            # audited tick adds IPC/commit delay without adding authorization.
            sim, wall = now()*1_000_000, time.monotonic_ns()
            snapshot = world_snapshot()
            step = feed("world", snapshot)
            require_world_accepted(step, snapshot)
            if chain.in_flight(guard("arm").get("accepted")):
                # One renewal in flight at a time, so a refusal is never hidden behind the next.
                return
            elapsed = max((time.monotonic_ns()-wall)/1e6, now()-sim//1_000_000)
            timing.sample("renewal_ms", elapsed)
            require_fresh_actuation(step,elapsed,now(),200,50)
            if elapsed >= 50:
                # Runner delay: never send a renewal the verifier must refuse.
                raise RunnerDelay("arm renewal", {"cause": "renewal_round_trip_at_or_over_limit",
                                                  "elapsed_ms": elapsed})
            if step.get("status",{}).get("active") is None:
                # Only a controller timing hold, shown by its own evidence, explains
                # the Rust arm:tracking revocation; settle() raises that verdict.
                settle(late, "arm renewal lost Rust authority")
                raise AssertionError("arm renewal lost Rust authority: " + json.dumps(step))
            heartbeat = token("arm","lease",digest,sim,wall,budget(step, sim))
            send_timely({"heartbeat": heartbeat}, heartbeat, "arm renewal", chain)
        positive_motion("arm", chain, late, lambda: abs(world.primary_joint()-initial[0]) > .08, 3,
                        "signed arm physically moves", renew)
        return {"ok": True, "moved_rad": abs(world.primary_joint()-initial[0])}
    def negative(target, case):
        stage = target + " " + case
        reset_grant = reset()[target]
        # Telemetry from here on carries any lapse of the reset lease before the packet leaves.
        lease_mark = len(telemetry(target))
        def lease_events():
            # Until the packet leaves only time may change at the controller; settle() raises
            # the verdict on anything else and on a visible lapse of the reset lease.
            lease_events.pending = reset_lease_events(target, stage, since(target, lease_mark) or [guard(target)],
                                                      reset_grant) is PENDING
            return False
        lease_events.pending = False
        chain = GrantChain(reset_grant["fields"], reset_grant["accepted"] - 1)
        # A harness delay before the packet leaves is inconclusive only once those events were judged.
        with fail_closed_settled(lambda: settle(lease_events, stage + " reset lease")):
            # Build before measuring rejection; the old world input cannot be restamped by relay.
            packet = base_packet() if target=="base" else arm_packet()[0]
            data=packet[target]
            if case=="unsigned":data["permit"]=""
            elif case=="altered":
                if target=="base":data["linear"]+=.1
                else:data["points"][0]["positions"][0]+=.1
            elif case=="signature":data["permit"]=data["permit"][:-1]+("0" if data["permit"][-1]!="0" else "1")
            elif case=="target":
                # A genuine, validly signed foreign-controller permit;
                # do not make the signature invalid by editing its body.
                foreign = "arm" if target == "base" else "base"
                fields = data["permit"].split(":")
                data["permit"] = token(foreign, "goal" if foreign == "arm" else "command",
                    fields[8], int(fields[4]) + SIM_ORDERING_BACKDATE_NS,
                    int(fields[5]), int(fields[6])-int(fields[4]))
            mark = len(telemetry(target))
            try:
                # Nothing was sent since the reset, so its nonce and both counters must be unchanged.
                witnessed = wait_row(target, lambda row: row.get("nonce") == reset_grant["nonce"]
                                     and row.get("accepted") == reset_grant["accepted"]
                                     and row.get("rejected") == reset_grant["rejected"]
                                     and row.get("holding") is False
                                     and row.get("reason") == "accepted"
                                     and 0 <= time.monotonic_ns() - row.get("published_wall_ns", 0) < VERIFIER_WINDOW_NS
                                     and time.monotonic_ns() < reset_grant["wall_end_ns"]
                                     and now() * 1_000_000 < reset_grant["sim_end_ns"],
                                     .2, stage + " live reset before negative packet")
            except TimeoutError:
                # Inconclusive only if nothing but time changed: precondition_miss judges any
                # lapse of the reset lease by the controller's own evidence and raises either
                # verdict; it returns False only while the arm's hold is not visible yet.
                wait_for(lambda: precondition_miss(target, stage, since(target, lease_mark) or [guard(target)],
                                                   reset_grant, time.monotonic_ns(), now() * 1_000_000),
                         1, processes, stage + " reset lease lapse evidence")
                raise AssertionError(stage + ": reset lease judgement returned without a verdict")
            prior = witnessed.get("rejected", 0)
            accepted_before = witnessed.get("accepted", 0)
            negative_witness = {"nonce_before": witnessed["nonce"],
                "holding_before": witnessed["holding"], "reason_before": witnessed["reason"],
                "before_published_wall_ns": witnessed["published_wall_ns"],
                "accepted_before": accepted_before, "rejected_before": prior,
                "lease_wall_end_ns": reset_grant["wall_end_ns"]}
            pose, joints=world.pose(),world.arm_positions()
            stale = None
            if case=="delay":
                # Deliberately stale signed origins exercise freshness while the
                # independent reset lease remains live. Never wait for the reset
                # itself to expire and count that as protection.
                data["permit"], stale = stale_permit(target, "command" if target == "base" else "goal",
                                                     data["permit"].split(":")[8], stage)
            if case in ("signature", "replay"):
                # Signature is checked after freshness, and the replay's first copy
                # must be admitted: both need a permit the verifier still finds fresh.
                require_timely(data["permit"], stage)
            first_late = (fail_closed_check(target, chain, negative_witness["before_published_wall_ns"],
                                            stage + " first packet") if case == "replay" else None)
            first_sent = time.monotonic_ns()
            require_fresh_witness(stage, negative_witness["before_published_wall_ns"], first_sent)
            require_live(stage, reset_grant["fields"], first_sent, now() * 1_000_000)
        if case == "replay":
            chain.sent(permit_fields(data["permit"]))
        negative_witness["sent_wall_ns"] = first_sent
        # The lease the controller relies on when the refused packet arrives, and
        # the telemetry row after which any arm hold is new.
        governing, hold_after = reset_grant["fields"], negative_witness["before_published_wall_ns"]
        def check_motion():
            # Motion under a refused packet fails on every path, conclusive or not.
            wait_for(stopped,3,processes,"negative control physically stopped")
            until=time.monotonic()+.3
            wait_for(lambda:time.monotonic()>=until,2,processes,"negative control observation")
            drift=abs(world.pose()[0]-pose[0]) if target=="base" else max(abs(a-b) for a,b in zip(joints,world.arm_positions()))
            hold = None
            if target == "arm":
                # Arm goals travel on a reliable action, and its ingress counts every goal, even one
                # that arrives after a lapse (controller.cpp). So on every path, inconclusive ones
                # included, the arm must show this goal's counted refusal and prompt hold, and may not
                # move from that refusal on (arm_hold raises); a goal the controller has not counted
                # when arm_hold's 2 s wait ends fails.
                hold = arm_hold(stage, mark, negative_witness["sent_wall_ns"],
                                negative_witness["rejected_before"], hold_after)
            # Drift over the limit can be inconclusive only in the replay, where the admitted first
            # goal may move until the duplicate's refusal.
            return refused_motion(stage, drift, hold if case == "replay" else None), hold
        send(packet)
        replay_admission = None
        replay_sent = None
        if case=="replay":
            # Rejection alone cannot prove replay protection: require
            # this exact packet's first admission before duplicating it.
            # An inconclusive outcome still needs an arm controller to have counted the first copy.
            with goal_counted(lambda: require_counted(target, witnessed, stage + " first packet")):
                admitted = wait_row(target, lambda row: first_late() or (not first_late.pending
                             and row.get("published_wall_ns", 0) >= first_sent
                             and row.get("nonce") == signer.challenges[target]
                             and row.get("accepted", 0) == accepted_before + 1
                             and row.get("rejected", 0) == prior
                             and row.get("reason") == "accepted"
                             and row.get("holding") is False),
                             1, target + " replay first packet actually admitted")
            replay_admission = {"accepted_before": accepted_before,
                "accepted_after_first": admitted["accepted"],
                "rejected_before": prior, "rejected_after_first": admitted["rejected"],
                "first_sent_wall_ns": first_sent,
                "first_admission_reason": admitted["reason"],
                "nonce": admitted["nonce"],
                "first_packet_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest(),
                "first_admission_published_wall_ns": admitted["published_wall_ns"]}
            pose, joints = world.pose(), world.arm_positions()
            duplicate = copy.deepcopy(packet)
            replay_admission["replay_packet_sha256"] = hashlib.sha256(
                json.dumps(duplicate, sort_keys=True).encode()).hexdigest()
            # Read the witnessed row before stamping the send, never after it.
            before = guard(target)
            if before.get("holding") is not False:
                # Only a counted event can hold an admitted controller; let the first
                # packet's checker classify it with its own evidence.
                settle(first_late, stage + " first packet")
                raise AssertionError(stage + ": controller held after admitting the first copy without an event")
            first_grant = permit_fields(data["permit"])
            # As before the first copy: the first packet's events are judged before a harness delay.
            with fail_closed_settled(lambda: settle(first_late, stage + " first packet")):
                replay_sent = time.monotonic_ns()
                require_fresh_witness(stage + " duplicate", before.get("published_wall_ns", 0), replay_sent)
                require_live(stage + " duplicate", first_grant, replay_sent, now() * 1_000_000)
            negative_witness.update({"before_published_wall_ns": before["published_wall_ns"],
                "accepted_before": before["accepted"], "rejected_before": before["rejected"],
                "sent_wall_ns": replay_sent, "lease_wall_end_ns": first_grant["wall_end_ns"]})
            governing, hold_after = first_grant, before["published_wall_ns"]
            send(duplicate)
            replay_admission["replay_sent_wall_ns"] = replay_sent
        with motion_checked(check_motion):
            rejection, _ = wait_judged(target, mark, lambda rows: refusal(
                target, rows, negative_witness, NEGATIVE_REASONS[case], negative_timing(target, case),
                governing, stage, hold_after if target == "arm" else None),
                2, stage + " consumed and rejected at controller")
            negative_witness.update({"nonce_after": rejection["nonce"],
                "accepted_after": rejection["accepted"], "rejected_after": rejection["rejected"],
                "rejection_reason": rejection["reason"],
                "rejection_published_wall_ns": rejection["published_wall_ns"]})
            # refusal() already failed any admission or re-activation. A refusal seen too close
            # to the end of the lease it relied on cannot be told apart from a lapse lock.
            require_refused_inside_lease(target, stage, rejection, negative_witness["lease_wall_end_ns"])
            if stale is not None:
                require_refused_inside_stale_window(stage, rejection, stale)
            if replay_admission is not None:
                replay_admission.update({"rejected_after_replay": rejection["rejected"],
                    "accepted_after_replay": rejection["accepted"],
                    "replay_rejection_reason": rejection["reason"],
                    "rejection_published_wall_ns": rejection["published_wall_ns"]})
                if (replay_admission["accepted_after_replay"] != accepted_before + 1 or
                        rejection.get("nonce") != replay_admission["nonce"] or
                        replay_admission["rejection_published_wall_ns"] < replay_sent):
                    raise AssertionError("replay did not produce a fresh rejection after first admission")
        drift, hold = check_motion()
        row = {"ok":True,"controller_rejection_observed":True,
               "negative_admission": negative_witness, "drift":drift, **recover(target, stage)}
        if replay_admission is not None:
            row["replay_admission"] = replay_admission
        if hold is not None:
            row["controller_hold"] = hold
        if stale is not None:
            row["stale_permit"] = {key: stale[key] for key in ("sim_ns", "wall_ns", "sim_end_ns", "wall_end_ns")}
        require_report_row(target, case, row)
        return row
    def late_renewal():
        # Fail-closed renewal check: a renewal whose signed origins are 60 ms old when
        # it leaves (as in the delay case) is refused while the arm moves under a live
        # goal. The controller itself must hold the arm within a few updates of that
        # refusal and before the goal's lease could end, and the arm must stay put.
        stage = "arm renewal_delay"
        grant = reset()["arm"]
        chain = GrantChain(grant["fields"], grant["accepted"] - 1)
        initial = world.primary_joint()
        before = guard("arm")
        accepted = before.get("accepted", 0)
        late = fail_closed_check("arm", chain, grant["unlocked_wall_ns"], stage + " goal")
        with fail_closed_settled(lambda: settle(late, stage + " goal")):
            packet, digest = arm_packet()
            send_timely(packet, packet["arm"]["permit"], stage + " goal", chain)
        goal = chain.last()
        mark = len(telemetry("arm"))
        # An inconclusive outcome still needs the controller to have counted the goal. Its "not
        # seen moving" outcome can come before that count, so the event the controller then counted
        # is judged too: a refusal for another reason or a premature lock fails, never a retry.
        with goal_counted(lambda: (require_counted("arm", before, stage + " goal"),
                                   settle(late, stage + " goal"))):
            try:
                witnessed = wait_row("arm", lambda row: late() or (not late.pending
                    and row.get("accepted", 0) == accepted + 1 and row.get("holding") is False
                    and row.get("reason") == "accepted" and row.get("active_digest") == digest
                    and 0 <= time.monotonic_ns() - row.get("published_wall_ns", 0) < VERIFIER_WINDOW_NS
                    and abs(world.primary_joint() - initial) > .005
                    and time.monotonic_ns() < goal["wall_end_ns"] and now() * 1_000_000 < goal["sim_end_ns"]),
                    .2, stage + " arm moving under its live goal")
            except TimeoutError:
                settle(late, stage + " goal")
                if guard("arm").get("accepted", 0) not in (accepted, accepted + 1):
                    raise AssertionError(stage + " controller admitted unexpected traffic: " + json.dumps(guard("arm")))
                raise RunnerDelay(stage, {"cause": "goal_motion_not_witnessed_inside_goal_lease",
                                          "accepted_delta": guard("arm").get("accepted", 0) - accepted})
        moved = abs(world.primary_joint() - initial)
        hold_after = witnessed["published_wall_ns"]
        negative_witness = {"nonce_before": witnessed["nonce"], "holding_before": witnessed["holding"],
            "reason_before": witnessed["reason"], "before_published_wall_ns": hold_after,
            "accepted_before": witnessed["accepted"], "rejected_before": witnessed["rejected"],
            "lease_wall_end_ns": goal["wall_end_ns"]}
        # A harness delay before the late renewal leaves is inconclusive only once the goal's
        # own events were judged: a premature lapse of the goal lease fails.
        with fail_closed_settled(lambda: settle(late, stage + " goal")):
            stale, stale_fields = stale_permit("arm", "lease", digest, stage)
            sent = time.monotonic_ns()
            require_fresh_witness(stage, hold_after, sent)
            require_live(stage, goal, sent, now() * 1_000_000)
        negative_witness["sent_wall_ns"] = sent
        def check_motion():
            # From the refusal on, the arm must hold promptly and stay put, on every path.
            wait_for(stopped,3,processes,"arm stops after refusing a late renewal")
            settled = world.arm_positions()
            until=time.monotonic()+.3
            wait_for(lambda:time.monotonic()>=until,2,processes,"late renewal hold observation")
            drift = max(abs(a-b) for a,b in zip(settled, world.arm_positions()))
            return (refused_motion(stage, drift),
                    arm_hold(stage, mark, sent, negative_witness["rejected_before"], hold_after))
        send({"heartbeat": stale})
        with motion_checked(check_motion):
            rejection, held = wait_judged("arm", mark, lambda rows: refusal(
                "arm", rows, negative_witness, NEGATIVE_REASONS["renewal_delay"],
                negative_timing("arm", "renewal_delay"), goal, stage, hold_after),
                2, stage + " refused at controller")
            negative_witness.update({"nonce_after": rejection["nonce"],
                "accepted_after": rejection["accepted"], "rejected_after": rejection["rejected"],
                "rejection_reason": rejection["reason"],
                "rejection_published_wall_ns": rejection["published_wall_ns"]})
            require_refused_inside_lease("arm", stage, rejection, goal["wall_end_ns"])
            require_refused_inside_stale_window(stage, rejection, stale_fields)
            # refusal() already made a hold at the goal lease end inconclusive, so only the
            # "held before the renewal left" failure can fire here; the lapse branches are defensive.
            stop_ns = late_stop_verdict(stage, held, sent, goal)
        drift, hold = check_motion()
        row = {"ok": True, "controller_rejection_observed": True, "negative_admission": negative_witness,
               "moving_before_late_renewal": {"joint1_displacement_rad": moved},
               "controller_stop_wall_ns": stop_ns, "drift": drift,
               "controller_hold": dict(hold, lease_sim_end_ns=goal["sim_end_ns"]),
               "stale_permit": {key: stale_fields[key] for key in ("sim_ns", "wall_ns", "sim_end_ns", "wall_end_ns")},
               **recover("arm", stage)}
        require_report_row("arm", "renewal_delay", row)
        return row
    try:
        resetter, reset_log = start([sys.executable, str(REPO / "ros/gazebo/controller_reset.py"), "--stdio"],
            root, "controller_reset", processes, env, input_pipe=True)
        # Actor readiness is synchronization only, never evidence of admission
        # or blocking. Trusted counters and actual motion still decide verdicts.
        wait_for(lambda: '{"ready": true}' in (root / "controller_attacker.log").read_text(),
                 15, processes, "relay DDS endpoints discovered before positive control")
        # Distinct physical positive controls, through the relay's permitted DDS routes.
        results["base_positive"] = bounded_attempts("base_positive", base_positive, timing)
        wait_for(stopped, 3, processes, "base independent expiry")
        held_pose, expiry_wall = world.pose(), time.monotonic_ns()
        wait_for(lambda: guard("base").get("holding") is True and
                 guard("base").get("published_wall_ns", 0) >= expiry_wall + 300_000_000,
                 2, processes, "fresh base expiry hold observation")
        expiry_drift = math.dist(world.pose(), held_pose)
        if expiry_drift > .02:
            raise AssertionError("expired base drift")
        results["base_expiry"]={"ok":True,"old_goal_did_not_resume":True,
                               "expiry_hold_observed": True, "expiry_drift": expiry_drift, "attempt": 1}
        results["arm_positive"] = bounded_attempts("arm_positive", arm_positive, timing)
        wait_for(stopped,3,processes,"arm independent expiry")
        held = world.arm_positions()
        expiry_wall = time.monotonic_ns()
        until=time.monotonic()+.3
        wait_for(lambda:time.monotonic()>=until,2,processes,"arm expiry settling")
        if max(abs(a-b) for a,b in zip(held,world.arm_positions()))>.02:
            raise AssertionError("expired arm drift")
        wait_for(lambda: guard("arm").get("holding") is True and
                 guard("arm").get("published_wall_ns", 0) >= expiry_wall + 300_000_000,
                 2, processes, "fresh arm expiry hold observation")
        results["arm_expiry"]={"ok":True,"old_goal_did_not_resume":True,
                              "expiry_hold_observed": True, "attempt": 1,
                              "expiry_drift": max(abs(a-b) for a,b in zip(held,world.arm_positions()))}
        for target in ("base","arm"):
            for case in ("unsigned","altered","signature","replay","delay","target"):
                results[target+"_"+case] = bounded_attempts(target+"_"+case,
                    lambda target=target, case=case: negative(target, case), timing)
        results["arm_renewal_delay"] = bounded_attempts("arm_renewal_delay", late_renewal, timing)
        result={"ok":True,"scope":"gazebo_exact_action_permits_with_compromised_relay_uid",
            "attacker_uid":UIDS["relay"],"issuer":"independent_real_rust_fixture",
            "checks":results,"trust":"root host simulator controller measured-world adapter and authorizer"}
        result["relay_boundaries"] = {
            "signer_credentials_unreadable": json.loads((root / "principal-isolation.json").read_text())["relay"]["signer_credentials_unreadable"],
            "denied_services": json.loads((root / "role-permissions.json").read_text())["relay"]["denied_services"]}
        # Fail here, in the job that holds the logs and timing record, rather than
        # first in the alpha packaging job; never write passing-looking evidence.
        verdict = next(row for row in report({"ok": True, "controller_permits": result})["checks"]
                       if row["id"] == "controller_permits")
        if verdict["status"] != "passed":
            raise AssertionError("controller permit evidence fails the public report: " + json.dumps(verdict))
        (root/"controller-permits.json").write_text(json.dumps(result,indent=2))
        timing.complete = True
        return result
    finally:
        try:
            timing.write()
            if bridge is not None:
                bridge.close()
        finally:
            try:
                process = processes.pop("controller_attacker", None)
                if process is not None:
                    stop(process, force=True)
            finally:
                log.close()
                process = processes.pop("controller_reset", None)
                if process is not None:
                    stop(process, force=True)
                if reset_log is not None:
                    reset_log.close()
