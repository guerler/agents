"""A model that says what it is told, so a claim drives a tool through a real run.

Serves an OpenAI-compatible chat endpoint: each request takes the next scripted reply, a list of
tool calls or a text, and the run around it is the session's own, guards and all.
"""

import contextlib
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .entries import messages

_ids = itertools.count(1)


def _sse(reply):
    chunks = []
    if isinstance(reply, str):
        chunks.append({"choices": [{"index": 0, "delta": {"content": reply}}]})
        finish = "stop"
    else:
        for index, (name, args) in enumerate(reply):
            call = {"index": index, "id": f"call_{next(_ids)}", "type": "function"}
            call["function"] = {"name": name, "arguments": json.dumps(args)}
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]})
        finish = "tool_calls"
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {}})
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@contextlib.contextmanager
def scripted(replies):
    """Serve `replies` in order, then "done"; yields the base URL a session config takes."""
    queue = list(replies)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = _sse(queue.pop(0) if queue else "done").encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def tool_results(entries):
    """What each tool call of a run answered: its text, whether it failed, the guard that refused."""
    out = []
    for m in messages(entries):
        if m.get("role") == "toolResult":
            text = "".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text")
            guard = (m.get("details") or {}).get("guard")
            out.append({"content": text, "is_error": bool(m.get("isError")), **({"guard": guard} if guard else {})})
    return out
