import contextlib
import io
import json
import socket
import time
import unittest
from unittest.mock import patch

import permit_ipc
import permit_link


class PermitIPCTest(unittest.TestCase):
    def test_complete_and_terminal_frames_use_real_socket(self):
        for raw in (b'x' * 512 + b'\n', b'{"kind":"terminal","reason":"gate_denied"}\n'):
            left, right = socket.socketpair()
            with left, right:
                stages = {}
                permit_ipc.send_line(left, raw, time.monotonic() + .5, stages)
                self.assertEqual(permit_ipc.receive_line(right, time.monotonic() + .5, stages), raw)
                self.assertLessEqual(stages['response_sent_ns'], stages['response_received_ns'])

    def test_fragmentation_keeps_one_deadline_and_rejects_trailing_or_oversized_data(self):
        class Peer:
            def __init__(self, chunks, advance=.01):
                self.chunks, self.advance, self.timeouts = iter(chunks), advance, []
            def settimeout(self, timeout):
                self.timeouts.append(timeout)
            def recv(self, size):
                clock[0] += self.advance
                return next(self.chunks)
        with patch.object(permit_ipc.time, 'monotonic', lambda: clock[0]):
            clock = [0.]
            peer = Peer([b'{', b'}', b'\n'])
            self.assertEqual(permit_ipc.receive_line(peer, .05), b'{}\n')
            self.assertGreater(peer.timeouts[0], peer.timeouts[-1])
            clock[0] = 0.
            with self.assertRaises(TimeoutError):
                permit_ipc.receive_line(Peer([b'a', b'b', b'\n'], .02), .05)
            for chunks, error in (([b'{}\nx'], ValueError), ([b'x' * 513], ValueError),
                                  ([b'x', b''], RuntimeError)):
                clock[0] = 0.
                with self.assertRaises(error):
                    permit_ipc.receive_line(Peer(chunks), .05)

    def test_send_is_complete_or_terminal_and_charged_to_original_deadline(self):
        left, right = socket.socketpair()
        with left, right:
            for raw in (b'partial', b'{}\nextra\n', b'x' * 513 + b'\n'):
                with self.assertRaises(ValueError):
                    permit_ipc.send_line(left, raw, time.monotonic() + .5)
            with self.assertRaises(TimeoutError):
                permit_ipc.send_line(left, b'{}\n', time.monotonic() - .001)
        class SlowWrite:
            def settimeout(self, value):
                pass
            def sendall(self, raw):
                clock[0] += .06
        clock = [0.]
        with patch.object(permit_ipc.time, 'monotonic', lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                permit_ipc.send_line(SlowWrite(), b'{}\n', .05)

    def test_late_operational_response_never_executes_or_retries(self):
        clock, requests, executed, stopped = [0.], [], [], []
        class Link:
            current = {'sequence': 0}
            def __init__(self, *args):
                pass
            def query(self, op):
                return 'ON'
            def execute(self, request, permit, timeout):
                executed.append(request['op'])
            def stop(self):
                stopped.append(True)
            def close(self):
                pass
        class Peer:
            def connect(self, path):
                pass
            def settimeout(self, timeout):
                pass
            def sendall(self, raw):
                requests.append(json.loads(raw)['op'])
            def recv(self, size):
                if requests[-1] == 'RUN':
                    clock[0] += .06
                return b'{"kind":"permit","remaining_ms":50}\n'
            def close(self):
                pass
        with patch.object(permit_link, 'PermitLink', Link), \
                patch.object(permit_link.socket, 'socket', lambda *args: Peer()), \
                patch.object(permit_link.time, 'sleep', lambda value: None), \
                patch.object(permit_link.time, 'monotonic', lambda: clock[0]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(TimeoutError):
                permit_link.relay('fake-port', 'public-install', 'fake-socket')
        self.assertEqual(requests, ['BIND', 'ARM', 'RUN'])
        self.assertEqual(executed, ['BIND', 'ARM'])
        self.assertEqual(stopped, [True])
        self.assertIn('permit_ipc_failure', output.getvalue())
