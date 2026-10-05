"""Actual Gazebo controller admission under a compromised relay UID.

Independent root test fixtures own a real Rust enforcer and permit signer.
Synthetic clear-world semantics remain trusted. No test signer runs in relay.
"""
import copy
import json
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
from controller_permits import PermitSigner, IDLE, base_digest, arm_digest
from signing import Signer
from run_scenario import fixture
from product_model import ARM_JOINTS, arm_policy
from role_isolation import fresh_fixture, UIDS


def exercise(world, root, binary, roles, processes, start, stop, command, wait_for, env):
    """Never invoked unless the ordinary authorization process is already gone."""
    if "gate" in processes or abs(world.speed()) >= .03:
        raise AssertionError("permit probes require measured stop and no live authorizer")
    relay = processes.pop("relay")
    stop(relay, force=True)
    attacker, log = start([sys.executable, str(REPO / "ros/gazebo/controller_attack.py")],
        root, "controller_attacker", processes, roles.environment("relay", env), UIDS["relay"], input_pipe=True)
    lab = root / "permit-fixture"
    lab.mkdir()
    source = None
    bridge = None
    fixture_id = 0
    signer = PermitSigner(roles.directories["gate"] / "controller.key")
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
    def joints_snapshot():
        # Provisioning a fresh Rust fixture is synchronous. Wait for actual
        # fresh feedback afterwards; never relabel an old observation as fresh.
        wait_for(lambda: time.monotonic()-world.joint_received < .05 and
                 time.monotonic()-world.odom_received < .05,
                 2, processes, "fresh measured controller probe world")
        return [{"name":j,"position":world.joint.position[list(world.joint.name).index(j)],
                 "velocity":world.joint.velocity[list(world.joint.name).index(j)]} for j in ARM_JOINTS]
    def measured_stamp():
        return min(m.header.stamp.sec * 1000 + m.header.stamp.nanosec // 1_000_000
                   for m in (world.odom, world.joint))
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
        measured = joints_snapshot()
        feed("world", {"stamp_ms": measured_stamp(), "robot": {"pose": {"x": world.pose()[0], "y": world.pose()[1]},
            "yaw": world.heading(), "twist": {"linear": world.speed(), "angular": 0.0},
            "joints": measured}, "humans": [], "confidence": 1.0})
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
        trajectory.header.stamp.sec, trajectory.header.stamp.nanosec = divmod(sim, 1_000_000_000)
        for point in step["arm"]["execute"]["points"]:
            p = JointTrajectoryPoint(); p.positions = [float(v) for v in point["positions"]]
            p.time_from_start.sec, p.time_from_start.nanosec = divmod(point["time_from_start_ms"]*1_000_000, 1_000_000_000)
            trajectory.points.append(p)
        digest = arm_digest(trajectory)
        return {"arm": {"joints": ARM_JOINTS, "stamp": sim,
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
        send({"base": {"stamp": sim, "linear": 0.0, "angular": 0.0,
                       "permit": token("base", "reset", base_digest(msg), sim, wall, budget(step, sim))},
              "heartbeat": token("arm", "reset", IDLE, sim, wall, budget(step, sim))})
        wait_for(lambda: not guard("base").get("holding", True) and not guard("arm").get("holding", True),
            2, processes, "explicit signed controller reset")
    def stopped():
        return abs(world.speed()) < .03 and all(abs(world.joint.velocity[list(world.joint.name).index(j)]) < .03 for j in ARM_JOINTS)
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
        results["base_expiry"]={"ok":True,"old_goal_did_not_resume":True}
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
            # Feed an actual current measured world and tick the same Rust goal.
            measured = joints_snapshot()
            feed("world", {"stamp_ms": measured_stamp(), "robot": {"pose": {"x":world.pose()[0],"y":world.pose()[1]},
                "yaw":world.heading(),"twist":{"linear":world.speed(),"angular":0.0},
                "joints":measured},"humans":[],"confidence":1.0})
            sim, wall = now()*1_000_000, time.monotonic_ns()
            step = bridge.request({"k":"tick","t":now()})
            require_fresh_actuation(step,(time.monotonic_ns()-wall)/1e6,now(),200,50)
            if step.get("status",{}).get("active") is None:
                raise AssertionError("arm renewal lost Rust authority")
            send({"heartbeat":token("arm","lease",digest,sim,wall,budget(step, sim))})
        wait_for(lambda: abs(world.primary_joint()-initial[0]) > .08, 3, processes,
                 "signed arm physically moves", action=renew)
        results["arm_positive"]={"ok":True,"moved_rad":abs(world.primary_joint()-initial[0])}
        wait_for(stopped,3,processes,"arm independent expiry")
        held = world.arm_positions()
        until=time.monotonic()+.3
        wait_for(lambda:time.monotonic()>=until,2,processes,"arm expiry settling")
        if max(abs(a-b) for a,b in zip(held,world.arm_positions()))>.02:
            raise AssertionError("expired arm drift")
        results["arm_expiry"]={"ok":True,"old_goal_did_not_resume":True}
        for target in ("base","arm"):
            for case in ("unsigned","altered","signature","replay","delay","target"):
                reset()
                # Build before measuring rejection; the old world input cannot be restamped by relay.
                packet = base_packet() if target=="base" else arm_packet()[0]
                data=packet[target]
                if case=="unsigned":data["permit"]=""
                elif case=="altered":
                    if target=="base":data["linear"]+=.1
                    else:data["points"][0]["positions"][0]+=.1
                elif case=="signature":data["permit"]=data["permit"][:-1]+("0" if data["permit"][-1]!="0" else "1")
                elif case=="target":data["permit"]=data["permit"].replace("v1:"+target+"-","v1:"+("arm" if target=="base" else "base")+"-",1)
                elif case=="delay":time.sleep(.06)
                prior = guard(target).get("rejected",0)
                pose, joints=world.pose(),world.arm_positions()
                send(packet)
                if case=="replay":send(copy.deepcopy(packet))
                wait_for(lambda:guard(target).get("rejected",0)>prior and guard(target).get("holding") is True,
                    2,processes,target+" "+case+" consumed and rejected at controller")
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
                send(fresh)
                until=time.monotonic()+.1
                wait_for(lambda:time.monotonic()>=until,2,processes,"recovery remains locked")
                if guard(target).get("holding") is not True:raise AssertionError("automatic recovery")
                results[target+"_"+case]={"ok":True,"controller_rejection_observed":True,
                    "drift":drift,"recovery_did_not_rearm":True}
        result={"ok":True,"scope":"gazebo_exact_action_permits_with_compromised_relay_uid",
            "attacker_uid":UIDS["relay"],"issuer":"independent_real_rust_fixture",
            "checks":results,"trust":"root host simulator controller measured-world adapter and authorizer"}
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
