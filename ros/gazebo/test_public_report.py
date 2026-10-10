import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from live_stream import LiveHub, start_server
from public_report import (NEGATIVE_REASONS, PERMIT_MAX_ATTEMPTS, PERMIT_NEGATIVES, RECOVERY_REASONS,
                           report, render_report)


def ms(offset):
    """A controller wall-clock stamp (ns) offset ms into a run."""
    return 5_000_000_000 + offset * 1_000_000


def permit_cases():
    cases={target+'_'+case:{'ok':True,'controller_rejection_observed':True,
                           'recovery_did_not_rearm':True,'drift':0.,'attempt':1,
                           'recovery_rejection_observed':True,'recovery_drift':0.,
                           'recovery_rejection_reason':RECOVERY_REASONS[target]}
           for target, case in PERMIT_NEGATIVES}
    for target, case in PERMIT_NEGATIVES:
        cases[target + '_' + case]['negative_admission'] = {
            'nonce_before': 'b' * 32, 'nonce_after': 'b' * 32,
            'holding_before': False, 'reason_before': 'accepted',
            'accepted_before': 2, 'accepted_after': 2, 'rejected_before': 0, 'rejected_after': 1,
            'before_published_wall_ns': ms(110), 'sent_wall_ns': ms(120),
            'rejection_published_wall_ns': ms(130), 'lease_wall_end_ns': ms(200),
            'rejection_reason': NEGATIVE_REASONS[case]}
    # Deliberately stale permits are signed when sent: 60 ms old, 200 ms lease.
    for key in ('base_delay', 'arm_delay', 'arm_renewal_delay'):
        cases[key]['stale_permit'] = {'wall_ns': ms(60), 'wall_end_ns': ms(260),
                                      'sim_ns': 1_000_000_000, 'sim_end_ns': 1_200_000_000}
    # The arm's own hold after it refused (controller simulation clock, ms).
    for key in ('arm_replay', 'arm_renewal_delay'):
        cases[key]['controller_hold'] = {'refusal_stamp_ms': 1_100, 'hold_cutoff_ms': 1_110,
                                         'drift_after_refusal': 0.}
    cases['arm_renewal_delay']['controller_hold']['lease_sim_end_ns'] = 1_200_000_000
    cases['arm_renewal_delay'].update({'moving_before_late_renewal': {'joint1_displacement_rad': .01},
                                       'controller_stop_wall_ns': ms(125)})
    cases.update({'base_positive':{'ok':True,'moved_m':.04,'attempt':1},'arm_positive':{'ok':True,'moved_rad':.1,'attempt':1},
                  'base_expiry':{'ok':True,'old_goal_did_not_resume':True,'expiry_hold_observed':True,'expiry_drift':0.,'attempt':1},
                  'arm_expiry':{'ok':True,'old_goal_did_not_resume':True,'expiry_hold_observed':True,'expiry_drift':0.,'attempt':1}})
    for target in ('base', 'arm'):
        cases[target + '_replay']['replay_admission'] = {
            'accepted_before': 1, 'accepted_after_first': 2, 'accepted_after_replay': 2,
            'rejected_before': 0, 'rejected_after_first': 0, 'rejected_after_replay': 1,
            'first_sent_wall_ns': 100, 'first_admission_published_wall_ns': 110,
            'replay_sent_wall_ns': 120, 'rejection_published_wall_ns': 130,
            'first_packet_sha256': 'a' * 64, 'replay_packet_sha256': 'a' * 64,
            'first_admission_reason': 'accepted', 'replay_rejection_reason': 'sequence'}
    return cases


def permit_result(cases):
    return {'controller_permits':{'ok':True,'scope':'gazebo_exact_action_permits_with_compromised_relay_uid',
                                  'attacker_uid':2005,'checks':cases,
                                  'relay_boundaries': {
                                      'signer_credentials_unreadable': {name:True for name in
                                          ('controller.key','log.key','keystore/enclaves/haetae/gate/key.pem')},
                                      'denied_services':{name:True for name in
                                          ('/controller_manager/switch_controller','/controller_manager/load_controller',
                                           '/controller_manager/unload_controller','/controller_manager/configure_controller',
                                           '/controller_manager/cleanup_controller','/controller_manager/reload_controller_libraries',
                                           '/diff_drive_base_controller/set_parameters','/joint_trajectory_controller/set_parameters',
                                           '/diff_drive_base_controller/set_parameters_atomically',
                                           '/joint_trajectory_controller/set_parameters_atomically')}}}}


def permit_check(value):
    return next(row for row in report(value)['checks'] if row['id']=='controller_permits')


class PublicReportTest(unittest.TestCase):
    def test_permit_summary_needs_motion_and_every_negative_controller_observation(self):
        cases = permit_cases()
        value = permit_result(cases)
        def check():
            return permit_check(value)['status']
        self.assertEqual(check(),'passed')
        self.assertEqual(permit_check(value)['measurements'], {'checks': 17, 'inconclusive_attempts': 0})
        witness = cases['base_replay']['replay_admission']
        for field, invalid in (('accepted_after_first', 1), ('accepted_after_replay', 3),
                               ('rejected_after_first', 1), ('rejected_after_replay', 0),
                               ('accepted_before', True), ('rejection_published_wall_ns', 115),
                               ('first_admission_published_wall_ns', 90),
                               ('first_admission_reason', 'sequence'), ('replay_rejection_reason', 'expired'),
                               ('replay_packet_sha256', 'b' * 64)):
            original = witness[field]
            witness[field] = invalid
            self.assertEqual(check(), 'failed', field)
            witness[field] = original
        cases['base_replay'].pop('replay_admission')
        self.assertEqual(check(), 'failed')
        cases['base_replay']['replay_admission'] = witness
        for target, case in PERMIT_NEGATIVES:
            negative = cases[target + '_' + case]['negative_admission']
            for field, invalid in (('holding_before', True), ('reason_before', 'sequence'),
                    ('rejection_reason', 'expired'), ('accepted_after', 3), ('rejected_after', 2),
                    ('rejected_before', True), ('nonce_after', 'c' * 32),
                    ('lease_wall_end_ns', ms(130)), ('before_published_wall_ns', ms(121))):
                original = negative[field]; negative[field] = invalid
                self.assertEqual(check(), 'failed', target + '_' + case + '_' + field)
                negative[field] = original
            # Motion under the refused packet and after the recovery packet: at most 0.02, measured.
            entry = cases[target + '_' + case]
            for field in ('drift', 'recovery_drift'):
                for invalid in (.021, None):
                    original = entry[field]; entry[field] = invalid
                    self.assertEqual(check(), 'failed', target + '_' + case + '_' + field + repr(invalid))
                    entry[field] = original
            cases[target + '_' + case].pop('negative_admission')
            self.assertEqual(check(), 'failed')
            cases[target + '_' + case]['negative_admission'] = negative
        denied = value['controller_permits']['relay_boundaries']['denied_services']
        denied['/controller_manager/switch_controller'] = False
        self.assertEqual(check(),'failed')
        denied['/controller_manager/switch_controller'] = True
        cases['base_positive'].pop('moved_m')
        self.assertEqual(check(),'failed')
        cases['base_positive']['moved_m']=.04
        cases['arm_altered']['controller_rejection_observed']=False
        self.assertEqual(check(),'failed')
        cases['arm_altered']['controller_rejection_observed']=True
        cases.pop('arm_signature')
        self.assertEqual(check(),'failed')

    def test_retry_record_late_renewal_and_latch_reason_are_part_of_the_verdict(self):
        cases = permit_cases()
        value = permit_result(cases)
        self.assertEqual(permit_check(value)['status'], 'passed')
        # A conclusive pass on a bounded retry is still a pass, and is counted.
        cases['arm_positive']['attempt'] = PERMIT_MAX_ATTEMPTS
        cases['base_delay']['attempt'] = 2
        self.assertEqual(permit_check(value)['status'], 'passed')
        self.assertEqual(permit_check(value)['measurements'], {'checks': 17, 'inconclusive_attempts': 3})
        for invalid in (0, PERMIT_MAX_ATTEMPTS + 1, True, 1.0, '1', None):
            cases['arm_positive']['attempt'] = invalid
            self.assertEqual(permit_check(value)['status'], 'failed', repr(invalid))
        cases['arm_positive']['attempt'] = 1
        cases['base_expiry'].pop('attempt')
        self.assertEqual(permit_check(value)['status'], 'failed')
        cases['base_expiry']['attempt'] = 1
        # The latch, not freshness, must refuse the recovery packet.
        for target, wrong in (('base', 'freshness'), ('base', 'rejected'), ('arm', 'locked'), ('arm', None)):
            cases[target + '_unsigned']['recovery_rejection_reason'] = wrong
            self.assertEqual(permit_check(value)['status'], 'failed', target + str(wrong))
            cases[target + '_unsigned']['recovery_rejection_reason'] = RECOVERY_REASONS[target]
        late = cases['arm_renewal_delay']
        for field, invalid in (('controller_stop_wall_ns', ms(200)), ('controller_stop_wall_ns', ms(119)),
                               ('controller_stop_wall_ns', True), ('controller_stop_wall_ns', None),
                               ('moving_before_late_renewal', {'joint1_displacement_rad': .005}),
                               ('moving_before_late_renewal', {})):
            original = late[field]; late[field] = invalid
            self.assertEqual(permit_check(value)['status'], 'failed', field + repr(invalid))
            late[field] = original
        late['negative_admission']['rejection_reason'] = 'locked'
        self.assertEqual(permit_check(value)['status'], 'failed')
        late['negative_admission']['rejection_reason'] = 'freshness'
        self.assertEqual(permit_check(value)['status'], 'passed')
        cases.pop('arm_renewal_delay')
        self.assertEqual(permit_check(value)['status'], 'failed')

    def test_base_refusal_must_be_published_before_its_lease_could_lapse(self):
        # A refusal overwrites `expired` on a lapsed guard. The base has no hold stamps, so its
        # refusal counts only more than 20 ms before the lease's wall end; the arm's before the wall end.
        cases = permit_cases()
        value = permit_result(cases)
        for key, published, status in (('base_unsigned', ms(179), 'passed'), ('base_unsigned', ms(180), 'failed'),
                                       ('base_unsigned', ms(180) - 1, 'passed'), ('base_delay', ms(185), 'failed'),
                                       ('arm_unsigned', ms(200) - 1, 'passed'), ('arm_unsigned', ms(200), 'failed')):
            witness = cases[key]['negative_admission']
            original = witness['rejection_published_wall_ns']
            witness['rejection_published_wall_ns'] = published
            self.assertEqual(permit_check(value)['status'], status, key + repr(published))
            witness['rejection_published_wall_ns'] = original
        self.assertEqual(permit_check(value)['status'], 'passed')

    def test_stale_permit_window_and_arm_hold_are_part_of_the_verdict(self):
        cases = permit_cases()
        value = permit_result(cases)
        self.assertEqual(permit_check(value)['status'], 'passed')
        # Only the 50 ms age bound may refuse a deliberately stale permit: at least
        # 50 ms old when sent, inside the 200 ms lease bound, refused before its own ends.
        for key in ('base_delay', 'arm_delay', 'arm_renewal_delay'):
            stale = cases[key]['stale_permit']
            for field, invalid in (('wall_ns', ms(71)), ('wall_end_ns', ms(261)), ('wall_end_ns', ms(60)),
                                   ('sim_end_ns', 1_000_000_000), ('sim_end_ns', 1_200_000_001),
                                   ('wall_end_ns', ms(150)), ('wall_ns', -1), ('sim_ns', True), ('wall_ns', 1.0)):
                original = stale[field]; stale[field] = invalid
                self.assertEqual(permit_check(value)['status'], 'failed', key + field + repr(invalid))
                stale[field] = original
            cases[key].pop('stale_permit')
            self.assertEqual(permit_check(value)['status'], 'failed', key)
            cases[key]['stale_permit'] = stale
        # The arm must hold within three controller updates of its refusal row and
        # move at most 0.02 rad from it; the late renewal also before the goal's simulation end.
        for key in ('arm_replay', 'arm_renewal_delay'):
            hold = cases[key]['controller_hold']
            for field, invalid in (('hold_cutoff_ms', 1_131), ('drift_after_refusal', .021),
                                   ('drift_after_refusal', None), ('refusal_stamp_ms', None), ('hold_cutoff_ms', 1.0)):
                original = hold[field]; hold[field] = invalid
                self.assertEqual(permit_check(value)['status'], 'failed', key + field + repr(invalid))
                hold[field] = original
            cases[key].pop('controller_hold')
            self.assertEqual(permit_check(value)['status'], 'failed', key)
            cases[key]['controller_hold'] = hold
        hold = cases['arm_renewal_delay']['controller_hold']
        hold['lease_sim_end_ns'] = 1_120_000_000
        self.assertEqual(permit_check(value)['status'], 'failed')
        hold['lease_sim_end_ns'] = 1_120_000_001
        self.assertEqual(permit_check(value)['status'], 'passed')
        hold.pop('lease_sim_end_ns')
        self.assertEqual(permit_check(value)['status'], 'failed')
        hold['lease_sim_end_ns'] = 1_200_000_000
        # The base has no simulation stamps and needs no arm hold.
        self.assertNotIn('controller_hold', cases['base_replay'])
        self.assertEqual(permit_check(value)['status'], 'passed')

    def test_absent_partial_or_forged_summary_is_never_a_pass(self):
        self.assertEqual(report()["status"], "pending")
        for result in ({}, {"ok": True}, {"ok": True, "status": "passed"}, [], {"ok": 1}):
            self.assertNotEqual(report(result)["status"], "passed")
        value = report({"ok": True, "base_moved_m": .1, "private_seed": "SECRET", "world": {"key": "SECRET"},
                        "sensor_faults": {"disconnect": {"ok": True, "unknown": {"healthy": False},
                            "fault_to_zero_wall_ms": 180, "recovery_did_not_rearm": False}},
                        "compound_faults": {"ok": True, "status": "complete", "iterations": [
                            {"ok": True, "proposal_hz_requested": 40, "signed_proposals_after_fault": 100}]}})
        self.assertEqual(value["status"], "failed")
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertEqual(next(c for c in value["checks"] if c["id"] == "sensor_disconnect")["status"], "failed")

    def test_invalid_numbers_and_metadata_are_omitted(self):
        for invalid in (True, -1, float("nan"), float("inf"), 10**1000, "0"):
            value = report({"ok": True, "base_moved_m": invalid}, "<script>SECRET</script>", "SECRET/../")
            self.assertNotIn("SECRET", json.dumps(value, allow_nan=False))
            self.assertEqual(value["source_revision"], "unknown")
            self.assertFalse(value["checks"][0]["measurements"])

    def test_http_pending_failure_report_download_and_no_secret_export(self):
        hub = LiveHub()
        server = start_server(hub, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "/report.json", timeout=3)
        self.assertEqual(error.exception.code, 409)
        hub.publish({"kind": "phase", "label": "실험 준비 완료"})
        self.assertTrue(hub.status()["ready"])
        hub.publish({"kind": "result", "result": {"ok": False, "key": "SECRET"}})
        result_event = next(row for row in hub.after(0, timeout=0) if row["kind"] == "result")
        self.assertEqual(result_event["report_status"], "failed")
        with urlopen(base + "/report.json?file=world.key", timeout=3) as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            payload = response.read()
            self.assertNotIn(b"SECRET", payload)
            self.assertEqual(json.loads(payload)["status"], "failed")
        with urlopen(base + "/report", timeout=3) as response:
            self.assertIn("실패", response.read().decode())
        hub.fail({"kind": "error", "detail": "SECRET"})
        self.assertFalse(hub.status()["ready"])
        self.assertEqual(hub.public_report()["status"], "failed")
        self.assertNotIn("SECRET", render_report(hub.public_report()).decode())
