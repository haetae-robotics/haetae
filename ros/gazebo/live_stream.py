"""Browser stream for the running Gazebo reference.

Only explicitly selected telemetry and verdict fields are sent. Test signing
keys, raw world messages, and private fixture files never enter this server.
"""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit


STATIC = Path(__file__).resolve().parents[2] / "sim"


class LiveHub:
    def __init__(self):
        self._condition = threading.Condition()
        self._messages = deque(maxlen=512)
        self._sequence = 0
        self._viewers = 0
        self.viewer_seen = threading.Event()

    def publish(self, message):
        with self._condition:
            self._sequence += 1
            self._messages.append({"id": self._sequence, **message})
            self._condition.notify_all()

    def after(self, sequence, timeout=2):
        with self._condition:
            self._condition.wait_for(lambda: self._sequence > sequence, timeout)
            return [row for row in self._messages if row["id"] > sequence]

    def add_viewer(self):
        with self._condition:
            self._viewers += 1
            self.viewer_seen.set()

    def remove_viewer(self):
        with self._condition:
            self._viewers -= 1

    def status(self):
        with self._condition:
            return {"viewers": self._viewers, "last_id": self._sequence}


def start_server(hub, port, bind_host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            origin = self.headers.get("Origin")
            allowed = {f"http://127.0.0.1:{self.server.server_address[1]}",
                       f"http://localhost:{self.server.server_address[1]}"}
            if origin and origin not in allowed:
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == "/events":
                self._stream()
            elif path == "/health":
                self._send(json.dumps({"ok": True, **hub.status()}).encode(), "application/json")
            elif path in ("/", "/gazebo-live.html", "/gazebo-live.js",
                          "/gazebo-replay.html", "/gazebo-replay.js",
                          "/evidence/gazebo-snapshot.jsonl", "/evidence/gazebo-result.json"):
                name = "gazebo-live.html" if path == "/" else path[1:]
                content = (STATIC / name).read_bytes()
                mime = ("text/javascript" if name.endswith(".js") else
                        "application/json" if name.endswith(".json") else
                        "application/x-ndjson" if name.endswith(".jsonl") else
                        "text/html")
                self._send(content, mime + "; charset=utf-8")
            else:
                self.send_error(404)

        def _send(self, content, mime):
            self.send_response(200)
            self.send_header("Content-Type", mime)
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
                try:
                    sequence = max(0, int(self.headers.get("Last-Event-ID", "0")))
                except ValueError:
                    sequence = 0
                while True:
                    messages = hub.after(sequence)
                    if not messages:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    for message in messages:
                        sequence = message["id"]
                        body = json.dumps(message, separators=(",", ":"))
                        self.wfile.write(f"id: {sequence}\ndata: {body}\n\n".encode())
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
