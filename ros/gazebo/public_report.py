"""Allowlisted, unsigned simulator summary. Never export fixture files/keys."""
import html
import json
import math
import re
from safe_evidence import read_evidence


def mapping(value):
    return value if isinstance(value, dict) else {}


def number(value):
    return value if type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value) else None


def within(value, maximum):
    value = number(value)
    return value is not None and value <= maximum


def report(result=None, revision="unknown", run_id="unknown", failed=False):
    if mapping(result).get("profile") == "household_hazards":
        return household_report(result,revision,run_id,failed)
    completed = result is not None
    result = mapping(result)
    checks = []

    def check(identifier, title, present, passed, metrics=None):
        checks.append({"id": identifier, "title": title,
                       "status": "not_run" if not present else "passed" if passed else "failed",
                       "measurements": {key: value for key, raw in (metrics or {}).items()
                                        if (value := number(raw)) is not None}})

    motion = number(result.get("base_moved_m"))
    check("motion", "정상 명령으로 실제 시뮬레이터 이동", motion is not None, motion is not None and motion > .03,
          {"base_moved_m": motion})
    people = result.get("native_person_measurements")
    people = [mapping(row) for row in people] if isinstance(people, list) else []
    check("person", "실제 가상 라이다 측정과 사람 접근 정지", bool(people),
          all(any(row.get("case") == case and row.get("ok") is True for row in people)
              for case in ("base_stop", "arm_stop", "departure")))
    check("arm_bounds", "팔 명령 제한", "arm_out_of_bounds_denied" in result,
          result.get("arm_out_of_bounds_denied") is True)
    transport = mapping(result.get("transport_isolation"))
    principals = mapping(transport.get("principals"))
    flags = ("exec_inherits", "no_new_privs", "capabilities_empty", "forbidden_udp_not_received", "native_pose_request_blocked")
    check("transport", "Gazebo 직접 통신 차단 · 제한된 컨테이너 계정", bool(transport),
          transport.get("ok") is True and transport.get("root_native_request_observed_in_scan") is True and
          transport.get("root_forbidden_port_positive_control") is True and all(
              all(mapping(principals.get(uid)).get(flag) is True for flag in flags) and
              all(mapping(mapping(principals.get(uid)).get("denied")).get(kind) is True
                  for kind in ("tcp", "unix", "ipv6", "packet"))
              for uid in ("2001", "2002", "2003", "2004", "2005", "65534")))
    attacks = mapping(result.get("attack_probes"))
    for name, title in (("direct", "ROS 바퀴 직접 명령 차단"), ("world", "ROS 사람 정보 위조 차단"),
                        ("replay", "명령 재전송 거부 · 별도 실행기"), ("signature", "서명 변조 거부 · 별도 실행기")):
        row = mapping(attacks.get(name))
        check(name, title, bool(row), row.get("blocked") is True)
    for name, title in (("disconnect", "라이다 연결 끊김 정지"), ("coverage", "라이다 검증 표적 손실 정지")):
        row = mapping(mapping(result.get("sensor_faults")).get(name))
        check("sensor_" + name, title, bool(row), row.get("ok") is True and
              mapping(row.get("unknown")).get("healthy") is False and row.get("recovery_did_not_rearm") is True and row.get("post_recovery_proposal_rejected_by_engine") is True and
              within(row.get("fault_to_zero_wall_ms"), 400), {"zero_wall_ms": row.get("fault_to_zero_wall_ms")})
    for name in ("kill", "stall", "delay"):
        row = mapping(mapping(result.get("arm_faults")).get(name))
        check("arm_" + name, "팔 독립 정지 · " + name, bool(row), row.get("ok") is True and
              row.get("old_goal_did_not_resume") is True and within(row.get("pre_fault_lease_age_wall_ms"), 100) and
              within(row.get("pre_fault_lease_age_sim_ms"), 150) and within(row.get("fault_to_hold_wall_ms"), 400) and
              within(row.get("fault_to_hold_sim_ms"), 320) and within(row.get("post_stop_drift_rad"), .02),
              {"hold_wall_ms": row.get("fault_to_hold_wall_ms")})
    compound = mapping(result.get("compound_faults"))
    rows = compound.get("iterations")
    rows = [mapping(row) for row in rows] if isinstance(rows, list) else []
    rates = {row.get("proposal_hz_requested") for row in rows if type(row.get("proposal_hz_requested")) is int}
    check("compound", "복합 장애 반복 · 요청 압력 + 센서 끊김 + 처리 지연", bool(compound),
          compound.get("ok") is True and compound.get("status") == "complete" and len(rows) >= 6 and
          all(sum(row.get("proposal_hz_requested") == rate for row in rows) >= 2 for rate in (40, 80, 100)) and all(row.get("ok") is True and row.get("moving_positive_control") is True and
              row.get("signer_stop_observed") is True and row.get("recovery_did_not_rearm") is True and
              row.get("post_recovery_proposal_rejected_by_engine") is True and
              number(row.get("engine_rejections_after_zero")) is not None and row["engine_rejections_after_zero"] >= 10 and
              within(row.get("fault_to_zero_wall_ms"), 400) for row in rows),
          {"iterations": len(rows), "max_zero_wall_ms": max((value for row in rows if (value := number(row.get("fault_to_zero_wall_ms"))) is not None), default=None)})
    check("audit", "봉인된 사건 기록 확인", "sillok_incident_snapshot_fully_sealed" in result,
          result.get("sillok_incident_snapshot_fully_sealed") is True)
    check("base_deadman", "게이트 종료 뒤 측정된 바퀴 정지", "gate_kill_to_base_stop_wall_ms" in result,
          within(result.get("gate_kill_to_base_stop_wall_ms"), 3000),
          {"stopped_observation_wall_ms": result.get("gate_kill_to_base_stop_wall_ms")})
    permits = mapping(result.get("controller_permits"))
    permit_checks = mapping(permits.get("checks"))
    boundaries = mapping(permits.get("relay_boundaries"))
    signer_denied = mapping(boundaries.get("signer_credentials_unreadable"))
    services_denied = mapping(boundaries.get("denied_services"))
    expected = {"base_positive", "arm_positive", "base_expiry", "arm_expiry"} | {
        target + "_" + case for target in ("base", "arm")
        for case in ("unsigned", "altered", "signature", "replay", "delay", "target")}
    check("controller_permits", "바퀴·팔 제어기의 동작별 허가 검사 · 침해된 전달자 계정", bool(permits),
          permits.get("ok") is True and permits.get("attacker_uid") == 2005 and
          permits.get("scope") == "gazebo_exact_action_permits_with_compromised_relay_uid" and
          all(signer_denied.get(name) is True for name in
              ("controller.key", "log.key", "keystore/enclaves/haetae/gate/key.pem")) and
          all(services_denied.get(name) is True for name in
              ("/controller_manager/switch_controller", "/controller_manager/load_controller",
               "/controller_manager/unload_controller", "/controller_manager/configure_controller",
               "/controller_manager/cleanup_controller", "/controller_manager/reload_controller_libraries",
               "/diff_drive_base_controller/set_parameters", "/joint_trajectory_controller/set_parameters",
               "/diff_drive_base_controller/set_parameters_atomically",
               "/joint_trajectory_controller/set_parameters_atomically")) and
          set(permit_checks) == expected and all(mapping(row).get("ok") is True for row in permit_checks.values()) and
          (number(mapping(permit_checks.get("base_positive")).get("moved_m")) or 0) > .03 and
          (number(mapping(permit_checks.get("arm_positive")).get("moved_rad")) or 0) > .08 and
          all(mapping(permit_checks.get(target + "_expiry")).get("old_goal_did_not_resume") is True
              for target in ("base", "arm")) and
          all(mapping(permit_checks.get(target + "_" + case)).get("controller_rejection_observed") is True and
              mapping(permit_checks.get(target + "_" + case)).get("recovery_did_not_rearm") is True and
              mapping(permit_checks.get(target + "_" + case)).get("recovery_rejection_observed") is True and
              within(mapping(permit_checks.get(target + "_" + case)).get("recovery_drift"), .02) and
              within(mapping(permit_checks.get(target + "_" + case)).get("drift"), .02)
              for target in ("base", "arm") for case in ("unsigned", "altered", "signature", "replay", "delay", "target")),
          {"checks": len(permit_checks)})
    status = ("failed" if failed or (result and result.get("ok") is not True) or
              any(row["status"] == "failed" for row in checks) else
              "passed" if all(row["status"] == "passed" for row in checks) else "incomplete" if completed else "pending")
    return {"schema_version": 1, "scope": "simulator_evaluation_only", "status": status,
            "source_revision": revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision) else "unknown",
            "run_id": run_id if isinstance(run_id, str) and re.fullmatch(r"[a-z0-9_-]{1,64}", run_id) else "unknown",
            "notice": "시뮬레이터 평가용 알파입니다. 실물 로봇의 침해 방지·안전 인증을 입증하지 않습니다.",
            "trust": "root·호스트·시뮬레이터·인지 입력·컨트롤러·승인 서비스는 신뢰합니다. 중간 전달자 계정의 명령 변조는 별도로 시험합니다.",
            "evidence": "이 리포트는 로컬 실행의 서명되지 않은 요약입니다. 원본 로그와 CI 증거는 별도로 확인하세요.",
            "checks": checks}


def render_report(value):
    # Only values produced by report() are rendered; still escape every string.
    escape = lambda text: html.escape(str(text), quote=True)
    labels = {"passed": "통과", "failed": "실패", "not_run": "미실행", "pending": "대기", "incomplete": "일부 미실행"}
    rows = "".join(f"<tr><td>{escape(row['title'])}</td><td>{labels[row['status']]}</td><td><code>{escape(json.dumps(row['measurements']))}</code></td></tr>" for row in value["checks"])
    return ("<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
            "<title>HAETAE 검증 리포트</title><style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 20px;color:#22313b}"
            "table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:14px 8px;border-bottom:1px solid #ddd}p{line-height:1.7}code{overflow-wrap:anywhere}</style>"
            f"<h1>HAETAE 시뮬레이터 검증 리포트</h1><h2>{labels[value['status']]}</h2><p>{escape(value['notice'])}</p>"
            f"<p>{escape(value['trust'])}</p><p>{escape(value['evidence'])}</p><p>소스: <code>{escape(value['source_revision'])}</code><br>실행: {escape(value['run_id'])}</p>"
            f"<table><tr><th>검증</th><th>결과</th><th>측정</th></tr>{rows}</table><p><a href=/report.json>JSON 저장</a> · <a href=/>시뮬레이터로 돌아가기</a></p></html>").encode()


def household_report(result, revision="unknown", run_id="unknown", failed=False):
    def motion_passed(row):
        return (row.get("allowed") is True
                and row.get("mandatory_semantic_gate") is True
                and row.get("signed_arm_acceptance_observed") is True
                and row.get("accepted_waypoints_match") is True
                and within(row.get("max_joint_tracking_error_rad"), .05)
                and type(row.get("tracking_samples")) is int and row["tracking_samples"] >= 10
                and (number(row.get("measured_motion_rad")) or 0) > .1
                and isinstance(row.get("plan_sha256"), str)
                and re.fullmatch(r"[a-f0-9]{64}", row["plan_sha256"]) is not None)

    expected = {"human":"human:protected-volume","heat":"heat:hazardous-item",
                "electricity":"electricity:contact","water":"water:electrical-item",
                "chemicals":"chemicals:incompatible","fall":"fall:protected-volume"}
    rows = result.get("hazard_checks", [])
    rows = rows if isinstance(rows,list) else []
    checks = []
    for identifier,reason in expected.items():
        matches = [mapping(row) for row in rows if mapping(row).get("id") == identifier]
        row = matches[0] if len(matches)==1 else {}
        passed = (len(matches)==1 and row.get("blocked") is True and motion_passed(row)
                  and row.get("signed_gate_rejection_observed") is True
                  and row.get("reason")==reason and within(row.get("denied_drift_rad"),.02)
                  )
        if identifier=="chemicals":
            first=mapping(row.get("first_transfer"))
            passed = (passed and row.get("retained_contents")==["bleach"]
                      and row.get("item_label_sequence") == ["bleach", "ammonia"] and row.get("same_visual_prop") is True
                      and motion_passed(first) and motion_passed(mapping(row.get("retreat"))))
            for sequence in (first, mapping(row.get("retreat"))):
                steps = sequence.get("steps")
                passed = passed and isinstance(steps, list) and len(steps) == 2 and all(
                    motion_passed(mapping(step)) for step in steps)
        title = row.get("title") if isinstance(row.get("title"), str) else identifier
        checks.append({"id":identifier,"title":title + (" · 동일 병 모형의 종류 전환" if identifier == "chemicals" else ""),
                       "status":"not_run" if not matches else "passed" if passed else "failed",
                       "measurements":{k:v for k in ("denied_drift_rad","measured_motion_rad","max_joint_tracking_error_rad","tracking_samples") if (v:=number(row.get(k))) is not None}})
    controls=mapping(result.get("negative_controls"))
    for identifier,reason in {"stale":"perception:stale","unknown_item":"item:unknown","missing_coverage":"perception:coverage-unknown"}.items():
        row=mapping(controls.get(identifier))
        checks.append({"id":identifier,"title":identifier,"status":"not_run" if not row else
                       "passed" if row.get("allowed") is False and row.get("reason")==reason else "failed","measurements":{}})
    gate_controls = mapping(result.get("mandatory_gate_controls"))
    for identifier, reason in {"missing_binding": "household:missing-binding",
                               "wrong_revision": "household:binding-mismatch",
                               "wrong_item": "household:binding-mismatch"}.items():
        row = mapping(gate_controls.get(identifier))
        passed = (row.get("allowed") is False and row.get("reason") == reason
                  and row.get("signed_gate_rejection_observed") is True
                  and within(row.get("denied_drift_rad"), .02))
        checks.append({"id": "gate_" + identifier, "title": "필수 실행 검사 · " + identifier,
                       "status": "not_run" if not row else "passed" if passed else "failed",
                       "measurements": {"denied_drift_rad": row["denied_drift_rad"]} if number(row.get("denied_drift_rad")) is not None else {}})
    passed = result.get("ok") is True and all(row["status"]=="passed" for row in checks)
    return {"schema_version":1,"scope":"household_hazard_mandatory_gate_simulation","status":"failed" if failed or result.get("ok") is not True or any(row["status"]=="failed" for row in checks) else "passed" if passed else "incomplete",
            "source_revision":revision if isinstance(revision,str) and re.fullmatch(r"[a-f0-9]{40}",revision) else "unknown",
            "run_id":run_id if isinstance(run_id,str) and re.fullmatch(r"[a-z0-9_-]{1,64}",run_id) else "unknown","checks":checks,
            "notice":"중앙 Rust 게이트의 필수 생활 위험 검사 실험입니다. 가상 팔 제어기는 동작별 허가를 검사합니다. 실물 보호·인지·파지·화학 반응·사람 밀기 방지는 미검증입니다.",
            "trust":"root 소유 시험 어댑터와 주입된 물체·기기 상태를 신뢰합니다. 좌표와 관절은 Gazebo 측정입니다.",
            "evidence":"서명되지 않은 로컬 요약입니다. 기존 침투 방어·독립 정지 시험은 이 프로필에서 통과로 집계하지 않습니다."}


def failed_household_result(root):
    """Preserve bounded completed evidence while the overall run stays failed."""
    def read(name, fallback):
        try:
            return json.loads(read_evidence(root, name, max_bytes=1024 * 1024).decode("utf-8"))
        except (OSError, ValueError):
            return fallback
    rows = read("hazard-progress.json", [])
    rows = rows if isinstance(rows, list) else []
    active = mapping(read("hazard-diagnostics.json", {})).get("case")
    if active in ("human", "heat", "electricity", "water", "chemicals", "fall") and not any(mapping(row).get("id") == active for row in rows):
        rows.append({"id": active, "blocked": False, "allowed": False})
    return {"profile": "household_hazards", "ok": False, "hazard_checks": rows}
