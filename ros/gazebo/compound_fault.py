"""Repeat compound faults against the real secured Gazebo command path.

Bounded proposal bursts exercise a compromised permitted VLA writer. These
checks are not a general resource-exhaustion or worst-case latency guarantee.
"""
import json
import os
from pathlib import Path
import signal
import time

PROFILES = ((40, 0.06), (80, 0.14), (100, 0.22))


def exercise_compound(world, roles, processes, wait_for, repeats, evidence_path, stop_report):
    def counter():
        return roles.counter("vla")

    results = []
    evidence = {"ok": False, "status": "running", "iterations": results,
                "zero_budget_ms": 400, "world_age_budget_ms": 200,
                "scope": "bounded_simulator_compound_fault_matrix_not_general_dos"}
    evidence_path.write_text(json.dumps(evidence, indent=2))
    for index in range(repeats):
        hz, pause_seconds = PROFILES[index % len(PROFILES)]
        world.marker(f"복합 장애 시험 {index + 1}/{repeats}",
                     detail=f"초당 {hz}개 이동 요청 중 센서를 끊고 입력 처리를 지연시킵니다.")
        wait_for(lambda: world.sensor_info.get("healthy"), 5, processes, "compound healthy sensor")
        world.states.clear()
        world.outcomes.clear()
        wait_for(lambda: world.states and "vla" in world.states[-1][1]["armed"] and
                 world.controllers_unlocked() and
                 sum(1 for _, row in world.outcomes if row.get("decision", {}).get("verdict") == "yun"
                     and row.get("decision", {}).get("action", {}).get("type") == "stop") >= 2,
                 8, processes, "compound explicit rearm", action=lambda: world.propose_base(0.0))
        wait_for(lambda: world.speed() > 0.06 and world.commands[-1][1] > 0.08,
                 5, processes, "compound real moving positive control",
                 action=lambda: world.propose_base(0.12))
        before = counter()
        burst_at = time.monotonic()
        world.proposal_pipe.write(json.dumps({"burst": {"hz": hz, "seconds": 1.4}}).encode() + b"\n")
        world.proposal_pipe.flush()
        wait_for(lambda: counter() >= before + 3 and world.commands[-1][1] > 0.08 and
                 sum(1 for t, row in world.outcomes if t >= burst_at and
                     row.get("decision", {}).get("verdict") == "yun" and
                     row.get("decision", {}).get("action", {}).get("type") == "velocity") >= 3,
                 1, processes, "compound accepted proposal burst before fault")
        fault_counter = counter()
        signer = processes["source_world"]
        paused = False
        try:
            with world.perception.lock:
                world.perception.drop_frames = True
                fault_at = time.monotonic()
                fault_ms = world.get_clock().now().nanoseconds // 1_000_000
            os.kill(signer.pid, signal.SIGSTOP)
            paused = True
            wait_for(lambda: "\nState:\tT" in Path(f"/proc/{signer.pid}/status").read_text(),
                     1, processes, "compound signer actually stopped")
            time.sleep(pause_seconds)
            os.kill(signer.pid, signal.SIGCONT)
            paused = False
            def sensor_stop():
                return stop_report(world.states, world.outcomes, world.zero_commands, fault_at, fault_ms)

            wait_for(lambda: not world.sensor_info.get("healthy") and sensor_stop() and abs(world.speed()) < 0.03,
                     3, processes, "compound authenticated stop under continuous proposals")
            stopped = sensor_stop()
            zero_at = stopped["zero_at"]
            stopped_at = time.monotonic()
            if zero_at - fault_at > 0.4 or world.sensor_info.get("healthy"):
                raise AssertionError("compound fault zero_ms=" + str(round((zero_at-fault_at)*1000,1)) +
                                     " sensor=" + json.dumps(world.sensor_info))
            # Keep the sensor down until the bounded burst has ended. Receiving
            # more signed proposals after the fault is a mandatory attack control.
            wait_for(lambda: time.monotonic() >= burst_at + 1.55,
                     2, processes, "compound burst completes while stopped")
            after = counter()
            engine_rejections = sum(1 for t, row in world.outcomes if t >= zero_at and
                row.get("rejected", {}).get("error") == "source must send a zero command to arm")
            if after < fault_counter + 10 or engine_rejections < 10 or abs(world.speed()) >= 0.03 or any(
                    t > zero_at + 0.02 and abs(value) > 0.03 for t, value in world.commands):
                raise AssertionError("compound proposal burst was absent or motion continued")
            with world.perception.lock:
                world.perception.drop_frames = False
            wait_for(lambda: world.sensor_info.get("healthy"), 5, processes, "compound sensor recovery")
            recovered_at = time.monotonic()
            world.propose_base(0.12)
            wait_for(lambda: counter() > after and any(t >= recovered_at and
                row.get("rejected", {}).get("error") == "source must send a zero command to arm"
                for t, row in world.outcomes), 2, processes, "compound post-recovery proposal received and rejected by engine")
            wait_for(lambda: time.monotonic() >= recovered_at + 0.35,
                     1, processes, "compound recovery observation")
            if (abs(world.speed()) >= 0.03 or "vla" in world.states[-1][1]["armed"] or
                    any(t >= recovered_at and abs(value) > 0.03 for t, value in world.commands)):
                raise AssertionError("compound recovery automatically resumed motion")
            row = {"ok": True, "iteration": index + 1, "proposal_hz_requested": hz,
                   "signed_proposals_observed": after - before,
                   "signed_proposals_after_fault": after - fault_counter,
                   "engine_rejections_after_zero": engine_rejections,
                   "post_recovery_proposal_rejected_by_engine": True,
                   "world_signer_pause_ms": round(pause_seconds * 1000),
                   "signer_stop_observed": True, "sensor": "gazebo_gpu_lidar_disconnect",
                   "moving_positive_control": True, "stop_reason": stopped["stop_reason"],
                   "fault_to_zero_wall_ms": round((zero_at - fault_at) * 1000, 1),
                   "fault_to_stopped_observation_wall_ms": round((stopped_at - fault_at) * 1000, 1),
                   "recovery_did_not_rearm": True}
            results.append(row)
            evidence_path.write_text(json.dumps(evidence, indent=2))
            world._emit("compound_result", **row)
        except Exception:
            evidence.update({"status": "failed", "failed_iteration": index + 1})
            evidence_path.write_text(json.dumps(evidence, indent=2))
            raise
        finally:
            if paused:
                os.kill(signer.pid, signal.SIGCONT)
            with world.perception.lock:
                world.perception.drop_frames = False
    evidence.update({"ok": True, "status": "complete"})
    evidence_path.write_text(json.dumps(evidence, indent=2))
    return evidence
