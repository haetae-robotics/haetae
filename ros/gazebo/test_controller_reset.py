"""Execute reset startup and its parent wait with scripted ROS service discovery."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


class ResetStartupTest(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        self.discovery_delay = 8
        self.future = mock.Mock()
        self.future.done.return_value = True
        self.future.result.return_value = SimpleNamespace(ok=True)
        self.client = mock.Mock()
        self.client.wait_for_service.side_effect = self.discover
        self.client.call_async.return_value = self.future
        self.node = mock.Mock()
        self.node.create_client.return_value = self.client
        self.rclpy = SimpleNamespace(init=mock.Mock(), try_shutdown=mock.Mock(),
                                    spin_until_future_complete=mock.Mock())
        request_type = lambda: SimpleNamespace(timeout=SimpleNamespace(sec=0))
        stubs = {"rclpy": self.rclpy, "rclpy.node": SimpleNamespace(Node=lambda name: self.node),
                 "controller_manager_msgs.srv": SimpleNamespace(
                     SwitchController=SimpleNamespace(Request=request_type))}
        spec = importlib.util.spec_from_file_location("reset_under_test", HERE / "controller_reset.py")
        self.reset = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, stubs):
            spec.loader.exec_module(self.reset)
        self.output = io.StringIO()

    def discover(self, timeout_sec):
        self.elapsed += min(timeout_sec, self.discovery_delay)
        return self.discovery_delay <= timeout_sec

    def run_reset(self, *args, stdin=None):
        with mock.patch.object(sys, "argv", ["controller_reset.py", *args]), \
                mock.patch.object(sys, "stdin", stdin or io.StringIO("1\n")), \
                contextlib.redirect_stdout(self.output):
            self.reset.main()

    def assert_cleaned_up(self):
        self.node.destroy_node.assert_called_once_with()
        self.rclpy.try_shutdown.assert_called_once_with()

    def test_delayed_discovery_announces_ready_before_consuming_persistent_request(self):
        def requests():
            self.assertEqual(self.elapsed, 8)
            self.assertEqual(json.loads(self.output.getvalue()), {"ready": True})
            self.client.call_async.assert_not_called()
            yield "1\n"

        self.run_reset("--stdio", stdin=requests())
        self.assertEqual([json.loads(line) for line in self.output.getvalue().splitlines()],
                         [{"ready": True}, {"reset_complete": 1}])
        calls = [call.args[0] for call in self.client.call_async.call_args_list]
        self.assertEqual(calls[0].deactivate_controllers, list(self.reset.CONTROLLERS))
        self.assertEqual(calls[1].activate_controllers, list(self.reset.CONTROLLERS))
        self.assertTrue(all(request.timeout.sec == 2 and request.strictness == 2 for request in calls))
        self.assertEqual([call.kwargs for call in self.rclpy.spin_until_future_complete.call_args_list],
                         [{"timeout_sec": 5}, {"timeout_sec": 5}])
        self.assert_cleaned_up()

    def test_one_shot_waits_for_delayed_discovery_then_switches(self):
        self.run_reset("joint_trajectory_controller", "active")
        self.assertEqual(self.elapsed, 8)
        self.assertEqual(self.output.getvalue(), "joint_trajectory_controller active\n")
        self.client.call_async.assert_called_once()
        self.assert_cleaned_up()

    def test_missing_service_is_bounded_without_ready_or_switch(self):
        self.discovery_delay = 100
        with self.assertRaisesRegex(RuntimeError, "service unavailable"):
            self.run_reset("--stdio")
        self.assertEqual(self.elapsed, 15)
        self.assertEqual(self.output.getvalue(), "")
        self.client.call_async.assert_not_called()
        self.assert_cleaned_up()

    def test_discovery_error_cleans_up_without_claiming_readiness(self):
        self.client.wait_for_service.side_effect = RuntimeError("discovery failed")
        with self.assertRaisesRegex(RuntimeError, "discovery failed"):
            self.run_reset("--stdio")
        self.assertEqual(self.output.getvalue(), "")
        self.client.call_async.assert_not_called()
        self.assert_cleaned_up()

    def test_failed_switch_never_claims_reset_complete_or_activates(self):
        for failure in ("timeout", "empty", "refused", "exception"):
            with self.subTest(failure=failure):
                self.setUp()
                self.future.done.return_value = failure != "timeout"
                if failure == "exception":
                    self.future.result.side_effect = RuntimeError("switch exception")
                elif failure in ("empty", "refused"):
                    self.future.result.return_value = None if failure == "empty" else SimpleNamespace(ok=False)
                with self.assertRaises(RuntimeError):
                    self.run_reset("--stdio")
                self.assertEqual(self.output.getvalue(), '{"ready": true}\n')
                self.client.call_async.assert_called_once()
                self.assert_cleaned_up()

    def test_failed_reactivation_never_claims_reset_complete(self):
        refused = mock.Mock()
        refused.done.return_value = True
        refused.result.return_value = SimpleNamespace(ok=False)
        self.client.call_async.side_effect = [self.future, refused]
        with self.assertRaisesRegex(RuntimeError, "controller switch failed"):
            self.run_reset("--stdio")
        self.assertEqual(self.output.getvalue(), '{"ready": true}\n')
        self.assertEqual(self.client.call_async.call_count, 2)
        self.assert_cleaned_up()


def actual_function(path, name, scope):
    tree = ast.parse(path.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


class ParentStartupTest(unittest.TestCase):
    def run_startup(self, marker_at=None, exit_at=None):
        """Run the actual startup through its first positive case, using the real wait_for."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "controller_reset.log").write_text("")
            (root / "controller_attacker.log").write_text('{"ready": true}\n')
            clock = SimpleNamespace(now=0.)

            def advance(seconds):
                clock.now += seconds
                if marker_at is not None and clock.now >= marker_at:
                    (root / "controller_reset.log").write_text('{"ready": true}\n')

            process = SimpleNamespace(returncode=1, stdin=mock.Mock(),
                                      poll=lambda: 1 if exit_at is not None and clock.now >= exit_at else None)
            processes = {"controller_reset": process}
            positive = mock.Mock(return_value={"ok": True})
            scope = {"time": SimpleNamespace(monotonic=lambda: clock.now, sleep=advance)}
            wait_for = actual_function(HERE / "run_reference.py", "wait_for", scope)
            scope.update(root=root, processes=processes, env={}, sys=sys, REPO=HERE.parents[1],
                         start=lambda *args, **kwargs: (process, io.StringIO()), wait_for=wait_for,
                         results={}, base_positive=positive, timing=None,
                         bounded_attempts=lambda label, case, timing: case())
            exercise = next(node for node in ast.parse((HERE / "controller_probes.py").read_text()).body
                            if isinstance(node, ast.FunctionDef) and node.name == "exercise")
            startup = next(node for node in exercise.body if isinstance(node, ast.Try))
            prefix = []
            for statement in startup.body:
                prefix.append(statement)
                if (isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Subscript)
                        and isinstance(statement.targets[0].value, ast.Name)
                        and statement.targets[0].value.id == "results"):
                    break
            try:
                exec(compile(ast.Module(body=prefix, type_ignores=[]), "actual-probe-startup", "exec"), scope)
            finally:
                self.elapsed, self.positive = clock.now, positive
                process.stdin.write.assert_not_called()

    def test_positive_case_waits_for_reset_service_readiness(self):
        self.run_startup(marker_at=14)
        self.assertGreaterEqual(self.elapsed, 14)
        self.assertLess(self.elapsed, 15)
        self.positive.assert_called_once_with()

    def test_live_but_unready_resetter_times_out_without_positive_case(self):
        with self.assertRaisesRegex(TimeoutError, "maintenance service discovered"):
            self.run_startup()
        self.assertGreaterEqual(self.elapsed, 20)
        self.assertLess(self.elapsed, 20.02)
        self.positive.assert_not_called()

    def test_resetter_exit_fails_immediately_without_positive_case(self):
        with self.assertRaisesRegex(RuntimeError, "controller_reset exited"):
            self.run_startup(exit_at=3)
        self.assertLess(self.elapsed, 3.02)
        self.positive.assert_not_called()

    def test_one_shot_parent_timeout_covers_discovery_and_switch(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
        scope = {"subprocess": SimpleNamespace(run=runner)}
        command = actual_function(HERE / "run_reference.py", "command", scope)
        with io.StringIO() as setup_log:
            # Use the real reset call, including any future explicit timeout override.
            root = mock.MagicMock()
            root.__truediv__.return_value.open.return_value = setup_log
            tree = ast.parse((HERE / "run_reference.py").read_text())
            call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "command"
                        and any(isinstance(value, ast.Constant) and value.value == "controller_reset.py"
                                for value in ast.walk(node)))
            scope.update(command=command, sys=sys, HERE=HERE, controller="joint_trajectory_controller",
                         state="active", root=root, gazebo_env={})
            eval(compile(ast.Expression(call), "actual-one-shot-reset", "eval"), scope)
        self.assertGreater(runner.call_args.kwargs["timeout"], 15 + 5)


if __name__ == "__main__":
    unittest.main()
