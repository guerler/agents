"""A reply that lands after its scenario is over has nowhere to go, and must not raise.

Handlers are daemon threads and `forward` waits on the provider for up to settings.MODEL_FORWARD_TIMEOUT_SECONDS, so
a scenario that times out leaves one in flight. It used to write into the closed gzip sink and
raise `ValueError: I/O operation on closed file` out of socketserver, printing a traceback per
timed-out scenario in the middle of a run.
"""

import gzip
import json
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.record import recording  # noqa: E402


class _Late:
    """Stands in for a handler that replies after the recording context has exited."""

    def __init__(self):
        self.server = None

    def capture(self, upstream, path):
        with recording(upstream, path):
            self.server = _running_recorder()


def _running_recorder():
    """The `_Recorder` the current recording context started."""
    for thread in threading.enumerate():
        target = getattr(thread, "_target", None)
        server = getattr(target, "__self__", None)
        if server is not None and hasattr(server, "exchanges"):
            return server
    raise AssertionError("no recorder thread found")


def test_a_reply_after_the_scenario_is_over_is_dropped_rather_than_raised(tmp_path):
    path = tmp_path / "run.jsonl.gz"
    late = _Late()
    late.capture("http://127.0.0.1:1", str(path))

    # The handler that was still forwarding when the context exited now answers.
    late.server.write("/v1/chat/completions", b"{}", 200, b"{}")

    assert late.server.closed is True


def test_what_arrived_in_time_is_still_recorded(tmp_path):
    path = tmp_path / "run.jsonl.gz"
    with recording("http://127.0.0.1:1", str(path)):
        server = _running_recorder()
        server.write("/v1/chat/completions", b'{"model":"m"}', 200, b'{"ok":true}')

    rows = [json.loads(line) for line in gzip.open(path, "rt")]
    assert [r["response"] for r in rows] == [{"ok": True}]
    assert rows[0]["request"] == {"model": "m"}


def test_a_caller_that_hung_up_does_not_raise_in_the_recorder(tmp_path, monkeypatch):
    import socket
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from lib import record

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            time.sleep(0.3)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    errors = []
    monkeypatch.setattr(record._Recorder, "handle_error", lambda self, request, address: errors.append(address))
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        with recording(f"http://127.0.0.1:{upstream.server_port}", str(tmp_path / "run.jsonl.gz")) as url:
            port = int(url.rsplit(":", 1)[1])
            caller = socket.create_connection(("127.0.0.1", port))
            caller.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            caller.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nhost: x\r\ncontent-length: 2\r\n\r\n{}")
            caller.close()
            time.sleep(0.8)
    finally:
        upstream.shutdown()
    assert errors == []
