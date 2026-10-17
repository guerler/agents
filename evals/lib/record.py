"""Every completion a run sends and receives, recorded at the network boundary.

The recorder stands in for the provider's base url. With `ai_provider` still named, the agent
resolves the registry's limits, compat and rate limit as always and only the destination
differs, so a recorded run submits what an unrecorded one submits.

A record is what went over the wire, verbatim, one JSON object per exchange. Files are gzipped
because a chat request carries the whole conversation again, and they stay on local disk.
"""

import gzip
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import settings


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        status, reply = self.server.forward(self.path, body, self.headers)
        self.server.write(self.path, body, status, reply)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class _Recorder(ThreadingHTTPServer):
    daemon_threads = True

    def configure(self, upstream, sink):
        self.upstream = upstream.rstrip("/")
        self.sink = sink
        self.exchanges = 0
        # Handlers are daemon threads, so `server_close()` does not wait for one still
        # forwarding. Closing guards the sink against a reply that lands after it is gone.
        self.closed = False
        self.lock = threading.Lock()

    def forward(self, path, body, headers):
        request = urllib.request.Request(
            f"{self.upstream}{path}",
            data=body,
            method="POST",
            headers={
                name: value
                for name, value in headers.items()
                # Host names the recorder; content-length is recomputed by urllib.
                if name.lower() not in ("host", "content-length", "accept-encoding")
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.MODEL_FORWARD_TIMEOUT_SECONDS) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            # The refusal is the interesting part of the record, so it is passed through.
            return err.code, err.read()
        except Exception as err:
            return 502, json.dumps({"error": f"recorder could not reach {self.upstream}: {err}"}).encode()

    def write(self, path, body, status, reply):
        with self.lock:
            if self.closed:
                # The scenario it belonged to is over -- it timed out, or the run moved on --
                # and there is nothing left to record it in.
                return
            self.exchanges += 1
            self.sink.write(
                json.dumps(
                    {
                        "exchange": self.exchanges,
                        "path": path,
                        "status": status,
                        "request": _decode(body),
                        "response": _decode(reply),
                    },
                    default=str,
                )
                + "\n"
            )
            self.sink.flush()

    def close(self):
        """Refuse further writes, so the sink can be closed with handlers still in flight."""
        with self.lock:
            self.closed = True


def _decode(raw):
    """The JSON the wire carried, or the bytes as text when it is not JSON."""
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


@contextmanager
def recording(upstream, path):
    """Serve as `upstream` for the duration, writing each exchange to `path`."""
    with gzip.open(path, "wt", encoding="utf-8") as sink:
        server = _Recorder(("127.0.0.1", 0), _Handler)
        server.configure(upstream, sink)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            # Before the sink goes out of scope: a handler may still be waiting on the
            # provider, and its reply has nowhere to go.
            server.close()
