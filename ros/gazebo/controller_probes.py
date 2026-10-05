"""Actual Gazebo controller admission under a compromised relay UID.

Independent root test fixtures own a real Rust enforcer and permit signer.
Synthetic clear-world semantics remain trusted. No test signer runs in relay.
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
from bridge import Bridge, require_fresh_actuation
from controller_permits import (PermitSigner, IDLE, SIM_ORDERING_BACKDATE_NS,
                                base_digest, arm_digest)
from signing import Signer
from run_scenario import fixture
from product_model import ARM_JOINTS, arm_policy
from role_isolation import fresh_fixture, UIDS


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
    def now():
        return world.get_clock().now().nanoseconds // 1_000_000
    def send(packet):
        nonlocal packet_id
        packet_id += 1
        attacker.stdin.write((json.dumps({**packet, "id": packet_id}) + "\n").encode())
        attacker.stdin.flush()
    def guard(target):
        rows = world.base_guard_states if target == "base" else world.guard_states
        return rows[-1][1] if rows else {}
    def feed(role, payload):
        envelope = source.sign(role, payload)
        return bridge.request({"k": "signed", "t": now(), "data": json.dumps(envelope)})
    def world_snapshot():
        # Provisioning a fresh Rust fixture is synchronous. Wait for actual
        # fresh feedback afterwards; never relabel an old observation as fresh.
        wait_for(lambda: time.monotonic()-world.joint_received < .05 and
                 time.monotonic()-world.odom_received < .05,
                 2, processes, "fresh measured controller probe world")
        return measured_world(world)
    def budget(step, sim):
        status = step["status"]
        remaining = min(200, 200 - status["world_age_ms"])
        if status.get("active_expires_ms") is not None:
            remaining = min(remaining, status["active_expires_ms"] - sim // 1_000_000)
        if remaining <= 0:
            raise AssertionError("controller probe Rust authority expired")
        return remaining * 1_000_000
    def approve(action):
        nonlocal proposal_id
        feed("world", world_snapshot())
        proposal_id += 1
        begin = time.monotonic_ns()
        stamp = now()
        step = feed("vla", {"id": proposal_id, "source": "vla", "timestamp_ms": stamp, "action": action})
        require_fresh_actuation(step, max((time.monotonic_ns()-begin)/1e6, now()-stamp), now(), 200, 50)
        if action["type"] == "velocity" and step["cmd"]["linear"] != action["linear"]:
            raise AssertionError("Rust base positive control denied")
        if action["type"] == "joint_trajectory" and not isinstance(step.get("arm"), dict):
            raise AssertionError("Rust arm positive control denied: " + json.dumps(step))
        return stamp * 1_000_000, begin, step
    def token(target, kind, digest, sim, wall, remaining=200_000_000):
        return signer.sign(target, kind, digest, sim, remaining, wall)
    def base_packet(kind="command", speed=.15):
        sim, wall, step = approve({"type": "velocity", "linear": speed, "angular": 0.0, "ttl_ms": 200})
        msg = TwistStamped()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(sim, 1_000_000_000)
        msg.twist.linear.x = float(step["cmd"]["linear"])
        return {"base": {"stamp": sim, "linear": msg.twist.linear.x, "angular": 0.0,
            "permit": token("base", kind, base_digest(msg), sim, wall, budget(step, sim))}}
    def arm_packet():
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
        send(packet)
        wait_for(lambda: not guard("base").get("holding", True) and not guard("arm").get("holding", True),
            2, processes, "explicit signed controller reset")
        return {target: {"nonce": signer.challenges[target],
                         "wall_end_ns": int(value.split(":")[7]),
                         "sim_end_ns": int(value.split(":")[6])}
                for target, value in (("base", packet["base"]["permit"]), ("arm", packet["heartbeat"]))}
    try:
        resetter, reset_log = start([sys.executable, str(REPO / "ros/gazebo/controller_reset.py"), "--stdio"],
            root, "controller_reset", processes, env, input_pipe=True)
        # Actor readiness is synchronization only, never evidence of admission
        # or blocking. Trusted counters and actual motion still decide verdicts.
        wait_for(lambda: '{"ready": true}' in (root / "controller_attacker.log").read_text(),
                 15, processes, "relay DDS endpoints discovered before positive control")
        # Distinct physical positive controls, through the relay's permitted DDS routes.
        reset()
        before = world.pose()
        wait_for(lambda: abs(world.pose()[0]-before[0]) > .03, 4, processes, "signed base physically moves",
                 action=lambda: send(base_packet()))
        results["base_positive"] = {"ok": True, "moved_m": abs(world.pose()[0]-before[0])}
        wait_for(stopped, 3, processes, "base independent expiry")
        held_pose, expiry_wall = world.pose(), time.monotonic_ns()
        wait_for(lambda: guard("base").get("holding") is True and
                 guard("base").get("published_wall_ns", 0) >= expiry_wall + 300_000_000,
                 2, processes, "fresh base expiry hold observation")
        expiry_drift = math.dist(world.pose(), held_pose)
        if expiry_drift > .02:
            raise AssertionError("expired base drift")
        results["base_expiry"]={"ok":True,"old_goal_did_not_resume":True,
                               "expiry_hold_observed": True, "expiry_drift": expiry_drift}
        reset()
        initial = world.arm_positions()
        accepted = guard("arm").get("accepted", 0)
        packet, digest = arm_packet(); send(packet)
        # Renewal uses a later sequence on a different DDS route. Wait for
        # controller admission of the goal before any renewal can overtake it.
        wait_for(lambda: guard("arm").get("accepted", 0) > accepted and
                 not guard("arm").get("holding", True),
                 1, processes, "trusted controller admits signed arm goal")
        def renew():
            # The world response already rechecks the same Rust goal. A second
            # audited tick adds IPC/commit delay without adding authorization.
            sim, wall = now()*1_000_000, time.monotonic_ns()
            step = feed("world", world_snapshot())
            elapsed = max((time.monotonic_ns()-wall)/1e6, now()-sim//1_000_000)
            require_fresh_actuation(step,elapsed,now(),200,50)
            if elapsed >= 50:
                raise AssertionError("arm renewal enforcement exceeded original admission budget")
            if step.get("status",{}).get("active") is None:
                raise AssertionError("arm renewal lost Rust authority: " + json.dumps(step))
            send({"heartbeat":token("arm","lease",digest,sim,wall,budget(step, sim))})
        wait_for(lambda: abs(world.primary_joint()-initial[0]) > .08, 3, processes,
                 "signed arm physically moves", action=renew)
        results["arm_positive"]={"ok":True,"moved_rad":abs(world.primary_joint()-initial[0])}
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
                              "expiry_hold_observed": True,
                              "expiry_drift": max(abs(a-b) for a,b in zip(held,world.arm_positions()))}
        for target in ("base","arm"):
            for case in ("unsigned","altered","signature","replay","delay","target"):
                reset_grants = reset()
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
                elif case=="delay":
                    # Deliberately stale signed origins exercise freshness while
                    # the independent reset lease remains live. Never wait for
                    # the reset itself to expire and count that as protection.
                    fields = data["permit"].split(":")
                    data["permit"] = token(target, "command" if target == "base" else "goal",
                        fields[8], int(fields[4]) + SIM_ORDERING_BACKDATE_NS - 60_000_000,
                        int(fields[5]) - 60_000_000, int(fields[6])-int(fields[4]))
                reset_grant = reset_grants[target]
                wait_for(lambda: guard(target).get("nonce") == reset_grant["nonce"]
                         and guard(target).get("holding") is False
                         and guard(target).get("reason") == "accepted"
                         and 0 <= time.monotonic_ns() - guard(target).get("published_wall_ns", 0) < 50_000_000
                         and time.monotonic_ns() < reset_grant["wall_end_ns"]
                         and now() * 1_000_000 < reset_grant["sim_end_ns"],
                         .2, processes, target + " " + case + " live reset before negative packet")
                prior = guard(target).get("rejected",0)
                accepted_before = guard(target).get("accepted", 0)
                negative_witness = {"nonce_before": guard(target)["nonce"],
                    "holding_before": guard(target)["holding"], "reason_before": guard(target)["reason"],
                    "before_published_wall_ns": guard(target)["published_wall_ns"],
                    "accepted_before": accepted_before, "rejected_before": prior,
                    "lease_wall_end_ns": reset_grant["wall_end_ns"]}
                pose, joints=world.pose(),world.arm_positions()
                first_sent = time.monotonic_ns()
                negative_witness["sent_wall_ns"] = first_sent
                send(packet)
                replay_admission = None
                if case=="replay":
                    # Rejection alone cannot prove replay protection: require
                    # this exact packet's first admission before duplicating it.
                    wait_for(lambda: guard(target).get("published_wall_ns", 0) >= first_sent
                             and guard(target).get("nonce") == signer.challenges[target]
                             and guard(target).get("accepted", 0) == accepted_before + 1
                             and guard(target).get("rejected", 0) == prior
                             and guard(target).get("reason") == "accepted"
                             and guard(target).get("holding") is False,
                             1, processes, target + " replay first packet actually admitted")
                    replay_admission = {"accepted_before": accepted_before,
                        "accepted_after_first": guard(target)["accepted"],
                        "rejected_before": prior, "rejected_after_first": guard(target)["rejected"],
                        "first_sent_wall_ns": first_sent,
                        "first_admission_reason": guard(target)["reason"],
                        "nonce": guard(target)["nonce"],
                        "first_packet_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest(),
                        "first_admission_published_wall_ns": guard(target)["published_wall_ns"]}
                    pose, joints = world.pose(), world.arm_positions()
                    replay_sent = time.monotonic_ns()
                    duplicate = copy.deepcopy(packet)
                    replay_admission["replay_packet_sha256"] = hashlib.sha256(
                        json.dumps(duplicate, sort_keys=True).encode()).hexdigest()
                    negative_witness.update({"before_published_wall_ns": guard(target)["published_wall_ns"],
                        "accepted_before": guard(target)["accepted"], "rejected_before": guard(target)["rejected"],
                        "sent_wall_ns": replay_sent, "lease_wall_end_ns": int(data["permit"].split(":")[7])})
                    send(duplicate)
                    replay_admission["replay_sent_wall_ns"] = replay_sent
                expected_reason = {"unsigned": "binding", "altered": "binding", "signature": "signature",
                                   "replay": "sequence", "delay": "freshness", "target": "binding"}[case]
                wait_for(lambda:guard(target).get("rejected",0) == prior + 1
                         and guard(target).get("holding") is True
                         and guard(target).get("reason") == expected_reason
                         and guard(target).get("published_wall_ns", 0) >= negative_witness["sent_wall_ns"],
                    2,processes,target+" "+case+" consumed and rejected at controller")
                negative_witness.update({"nonce_after": guard(target)["nonce"],
                    "accepted_after": guard(target)["accepted"], "rejected_after": guard(target)["rejected"],
                    "rejection_reason": guard(target)["reason"],
                    "rejection_published_wall_ns": guard(target)["published_wall_ns"]})
                if (negative_witness["accepted_after"] != negative_witness["accepted_before"] or
                        negative_witness["nonce_after"] != negative_witness["nonce_before"] or
                        negative_witness["rejection_published_wall_ns"] >= negative_witness["lease_wall_end_ns"]):
                    raise AssertionError("negative packet was not rejected under the witnessed live lease")
                if replay_admission is not None:
                    replay_admission.update({"rejected_after_replay": guard(target)["rejected"],
                        "accepted_after_replay": guard(target)["accepted"],
                        "replay_rejection_reason": guard(target)["reason"],
                        "rejection_published_wall_ns": guard(target)["published_wall_ns"]})
                    if (replay_admission["accepted_after_replay"] != accepted_before + 1 or
                            guard(target).get("nonce") != replay_admission["nonce"] or
                            replay_admission["rejection_published_wall_ns"] < replay_sent):
                        raise AssertionError("replay did not produce a fresh rejection after first admission")
                wait_for(stopped,3,processes,"negative control physically stopped")
                until=time.monotonic()+.3
                wait_for(lambda:time.monotonic()>=until,2,processes,"negative control observation")
                drift=abs(world.pose()[0]-pose[0]) if target=="base" else max(abs(a-b) for a,b in zip(joints,world.arm_positions()))
                if drift>.02:raise AssertionError(target+" "+case+" moved under invalid permit")
                # A new correctly signed command cannot clear the local lock.
                # A fresh independent Rust issuer may approve a new command,
                # but sends no controller reset. This tests the local latch
                # with a fresh signature/time, not just another stale replay.
                new_issuer()
                approve({"type":"stop"})
                fresh=base_packet() if target=="base" else arm_packet()[0]
                accepted_before = guard(target).get("accepted", 0)
                rejected_before = guard(target).get("rejected", 0)
                recovery_pose, recovery_joints = world.pose(), world.arm_positions()
                delivered_wall = time.monotonic_ns()
                send(fresh)
                wait_for(lambda: guard(target).get("published_wall_ns", 0) >= delivered_wall
                         and guard(target).get("rejected", 0) > rejected_before
                         and guard(target).get("holding") is True,
                         2, processes, "fresh recovery rejected at locked controller")
                until=time.monotonic()+.3
                wait_for(lambda:time.monotonic()>=until,2,processes,"recovery remains locked")
                recovery_drift = (math.dist(world.pose(), recovery_pose) if target == "base" else
                    max(abs(a-b) for a,b in zip(recovery_joints, world.arm_positions())))
                if (guard(target).get("holding") is not True or
                        guard(target).get("accepted", 0) != accepted_before or recovery_drift > .02):
                    raise AssertionError("automatic recovery")
                results[target+"_"+case]={"ok":True,"controller_rejection_observed":True,
                    "negative_admission": negative_witness,
                    "drift":drift,"recovery_did_not_rearm":True,
                    "recovery_rejection_observed":True, "recovery_drift": recovery_drift}
                if replay_admission is not None:
                    results[target+"_"+case]["replay_admission"] = replay_admission
        result={"ok":True,"scope":"gazebo_exact_action_permits_with_compromised_relay_uid",
            "attacker_uid":UIDS["relay"],"issuer":"independent_real_rust_fixture",
            "checks":results,"trust":"root host simulator controller measured-world adapter and authorizer"}
        result["relay_boundaries"] = {
            "signer_credentials_unreadable": json.loads((root / "principal-isolation.json").read_text())["relay"]["signer_credentials_unreadable"],
            "denied_services": json.loads((root / "role-permissions.json").read_text())["relay"]["denied_services"]}
        (root/"controller-permits.json").write_text(json.dumps(result,indent=2))
        return result
    finally:
        try:
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
