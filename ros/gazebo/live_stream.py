"""Browser stream for the running Gazebo reference.

Only explicitly selected telemetry and verdict fields are sent. Test signing
keys, raw world messages, and private fixture files never enter this server.
"""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
from uuid import uuid4
from public_report import report, render_report


STATIC = Path(__file__).resolve().parents[2] / "sim"


class LiveHub:
    def __init__(self):
        self._condition = threading.Condition()
        self._messages = deque(maxlen=512)
        self._milestones = deque(maxlen=64)
        self._sequence = 0
        self.session_id = uuid4().hex
        self._viewers = 0
        self.viewer_seen = threading.Event()
        self.start_requested = threading.Event()
        self._checkpoint = None
        self._checkpoint_state = None
        self._failure = None
        self._ready = False
        self._report = None

    def fail(self, message):
        with self._condition:
            self._checkpoint = None
            self._checkpoint_state = None
            self.publish(message)
            self._failure = self._messages[-1]
            self._ready = False
            self._report = report(revision=os.environ.get("HAETAE_REVISION", "unknown"),
                                  run_id=self.session_id, failed=True)

    def publish(self, message):
        with self._condition:
            self._sequence += 1
            row = {"id": self._sequence, **message}
            self._messages.append(row)
            if message.get("kind") == "phase" and message.get("label") == "실험 준비 완료":
                self._ready = True
            if message.get("kind") == "result":
                self._report = report(message.get("result"), os.environ.get("HAETAE_REVISION", "unknown"), self.session_id)
                row["report_status"] = self._report["status"]
            if message.get("kind") in ("phase", "decision", "attack_result", "result"):
                self._milestones.append(row)
            if message.get("kind") == "checkpoint":
                self._checkpoint_state = row
            self._condition.notify_all()

    def begin_checkpoint(self, *, label, detail, next_label, sim_ms):
        with self._condition:
            token = uuid4().hex
            self._checkpoint = token
            self.publish({"kind": "checkpoint", "waiting": True, "token": token,
                          "label": label, "detail": detail,
                          "next_label": next_label, "sim_ms": sim_ms})
            return token

    def checkpoint_active(self, token):
        with self._condition:
            return self._checkpoint == token

    def advance_checkpoint(self, token):
        with self._condition:
            if not token or self._checkpoint != token:
                return False
            self._checkpoint = None
            self.publish({"kind": "checkpoint", "waiting": False, "token": token})
            return True

    def after(self, sequence, timeout=2):
        with self._condition:
            self._condition.wait_for(lambda: self._sequence > sequence, timeout)
            # Telemetry is bounded, but a late browser still needs the phases
            # that unlock the start button and explain the current state.
            rows = {row["id"]: row for row in self._messages if row["id"] > sequence}
            rows.update({row["id"]: row for row in self._milestones
                         if row["id"] > sequence})
            if self._checkpoint_state and self._checkpoint_state["id"] > sequence:
                rows[self._checkpoint_state["id"]] = self._checkpoint_state
            if self._failure and self._failure["id"] > sequence:
                rows[self._failure["id"]] = self._failure
            return [rows[key] for key in sorted(rows)]

    def add_viewer(self):
        with self._condition:
            self._viewers += 1
            self.viewer_seen.set()

    def remove_viewer(self):
        with self._condition:
            self._viewers -= 1

    def status(self):
        with self._condition:
            return {"viewers": self._viewers, "last_id": self._sequence,
                    "failed": self._failure is not None, "ready": self._ready,
                    "started": self.start_requested.is_set(),
                    "report_status": self._report["status"] if self._report else "pending"}

    def public_report(self):
        with self._condition:
            return self._report


def start_server(hub, port, bind_host="127.0.0.1", gazebo_gui=False,
                 manual_start=False, attack_probes=False, secured_gazebo=False,
                 step_through=False):
    gui_port = int(os.environ.get("HAETAE_GUI_PORT", "6080"))
    if not 1 <= gui_port <= 65535:
        raise ValueError("HAETAE_GUI_PORT must be 1..65535")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def _origin_allowed(self):
            origin = self.headers.get("Origin")
            allowed = {f"http://127.0.0.1:{self.server.server_address[1]}",
                       f"http://localhost:{self.server.server_address[1]}"}
            host_allowed = {f"127.0.0.1:{self.server.server_address[1]}",
                            f"localhost:{self.server.server_address[1]}"}
            if self.headers.get("Host") not in host_allowed or (origin and origin not in allowed):
                self.send_error(403)
                return False
            return True

        def do_POST(self):
            if not self._origin_allowed():
                return
            if hub.status()["failed"]:
                self.send_error(409, "Simulation has stopped")
                return
            if urlsplit(self.path).path == "/advance" and step_through:
                # A token belongs to exactly one wait. Double clicks, stale
                # tabs and delayed requests cannot skip a subsequent scene.
                if not hub.advance_checkpoint(self.headers.get("X-Checkpoint-Token")):
                    self.send_error(409, "No matching waiting step")
                    return
                self._send(b'{"advanced":true}', "application/json")
                return
            if urlsplit(self.path).path != "/start" or not manual_start:
                self.send_error(404)
                return
            hub.start_requested.set()
            self._send(b'{"started":true}', "application/json")

        def do_GET(self):
            if not self._origin_allowed():
                return
            path = urlsplit(self.path).path
            if path == "/events":
                self._stream()
            elif path == "/health":
                status = hub.status()
                self._send(json.dumps({"ok": not status["failed"], **status}).encode(), "application/json")
            elif path in ("/report.json", "/report"):
                value = hub.public_report()
                if value is None:
                    self.send_error(409, "Report is available after completion or failure")
                elif path == "/report":
                    self._send(render_report(value), "text/html; charset=utf-8")
                else:
                    self._send(json.dumps(value, ensure_ascii=False, indent=2).encode(),
                               "application/json; charset=utf-8",
                               filename="haetae-simulator-report-" + hub.session_id + ".json")
            elif path == "/viewer-config":
                self._send(json.dumps({"gazebo_gui": gazebo_gui, "gazebo_gui_port": gui_port,
                                       "manual_start": manual_start,
                                       "attack_probes": attack_probes,
                                       "secured_gazebo": secured_gazebo,
                                       "step_through": step_through}).encode(), "application/json")
            elif path == "/assets/rosbot-xl.json":
                self._send((STATIC / "assets/rosbot-xl.json.gz").read_bytes(),
                           "application/json; charset=utf-8", encoding="gzip")
            elif path in ("/", "/gazebo-live.html", "/gazebo-live.js", "/gazebo-live.css",
                          "/brand/haetae/logo-light.svg", "/brand/haetae/favicon.svg",
                          "/assets/haetae-rig.json",
                          "/gazebo-scene.js", "/telemetry.js", "/product-rig.js", "/person-rig.js", "/vendor/three/three.module.js",
                          "/vendor/three/three.core.js", "/vendor/three/OrbitControls.js",
                          "/gazebo-replay.html", "/gazebo-replay.js",
                          "/evidence/gazebo-snapshot.jsonl", "/evidence/gazebo-result.json"):
                name = "gazebo-live.html" if path == "/" else path[1:]
                content = (STATIC / name).read_bytes()
                mime = ("text/javascript" if name.endswith(".js") else
                        "text/css" if name.endswith(".css") else
                        "image/svg+xml" if name.endswith(".svg") else
                        "application/json" if name.endswith(".json") else
                        "application/x-ndjson" if name.endswith(".jsonl") else
                        "text/html")
                self._send(content, mime + "; charset=utf-8")
            else:
                self.send_error(404)

        def _send(self, content, mime, encoding=None, filename=None):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            hub.add_viewer()
            try:
                last_id = self.headers.get("Last-Event-ID", "")
                try:
                    session_id, number = last_id.split(":", 1)
                    sequence = max(0, int(number)) if session_id == hub.session_id else 0
                except ValueError:
                    sequence = 0
                sequence = min(sequence, hub.status()["last_id"])
                while True:
                    messages = hub.after(sequence)
                    if not messages:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    for message in messages:
                        sequence = message["id"]
                        body = json.dumps(message, separators=(",", ":"))
                        self.wfile.write(f"id: {hub.session_id}:{sequence}\ndata: {body}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                hub.remove_viewer()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server((bind_host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
