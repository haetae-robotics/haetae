import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from live_stream import LiveHub, start_server
from public_report import report, render_report


class PublicReportTest(unittest.TestCase):
    def test_permit_summary_needs_motion_and_every_negative_controller_observation(self):
        cases={target+'_'+case:{'ok':True,'controller_rejection_observed':True,
                               'recovery_did_not_rearm':True,'drift':0.,
                               'recovery_rejection_observed':True,'recovery_drift':0.}
               for target in ('base','arm') for case in ('unsigned','altered','signature','replay','delay','target')}
        cases.update({'base_positive':{'ok':True,'moved_m':.04},'arm_positive':{'ok':True,'moved_rad':.1},
                      'base_expiry':{'ok':True,'old_goal_did_not_resume':True},
                      'arm_expiry':{'ok':True,'old_goal_did_not_resume':True}})
        value={'controller_permits':{'ok':True,'scope':'gazebo_exact_action_permits_with_compromised_relay_uid',
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
        def check():
            return next(row for row in report(value)['checks'] if row['id']=='controller_permits')['status']
        self.assertEqual(check(),'passed')
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
