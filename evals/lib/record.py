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

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # The session hung up first, as one past its budget does; its reply is recorded anyway.
            pass

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


def usage(path):
    """Tokens and cost a recorded run spent, summed over its exchanges; None without a record.

    A streamed reply states its usage in a final chunk, a plain one in its body. Cost is the
    provider's own figure where it reports one (OpenRouter does), never a price looked up here.
    """
    if not path:
        return None
    totals = {"exchanges": 0, "failedExchanges": 0, "promptTokens": 0, "completionTokens": 0, "cost": None}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            lines = list(source)
    except (OSError, EOFError):
        return None
    for line in lines:
        exchange = json.loads(line)
        totals["exchanges"] += 1
        if exchange.get("status", 200) >= 400:
            totals["failedExchanges"] += 1
        stated = _stated_usage(exchange.get("response"))
        if not stated:
            continue
        totals["promptTokens"] += stated.get("prompt_tokens") or 0
        totals["completionTokens"] += stated.get("completion_tokens") or 0
        if isinstance(stated.get("cost"), (int, float)):
            totals["cost"] = (totals["cost"] or 0) + stated["cost"]
    return totals


def _stated_usage(response):
    """The last usage a reply states, from a JSON body or a server-sent event stream."""
    if isinstance(response, dict):
        return response.get("usage")
    found = None
    for line in (response or "").splitlines():
        if line.startswith("data: {"):
            try:
                found = json.loads(line[6:]).get("usage") or found
            except ValueError:
                continue
    return found
