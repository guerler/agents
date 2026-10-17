"""The agent under test in its own process, spoken to over JSON lines."""

import asyncio
import json
import re

import subject

from .entries import digest

# A turn result carries the whole transcript, far past asyncio's 64 KiB line default.
LINE_LIMIT = 1 << 30


# What a failed session said last, kept for the error a run reports.
STDERR_TAIL = 40


class TurnFailed(Exception):
    """A turn the agent returned as failed; the status, when the provider stated one."""

    def __init__(self, message):
        super().__init__(message)
        stated = re.match(r"(\d{3})\b", message or "")
        self.status_code = int(stated.group(1)) if stated else None


class Session:
    """One agent session in its own process, spoken to over JSON lines.

    The agent publishes itself as a Node module (`subject.session()`); a request is one line,
    and its result is the line that answers it.
    """

    def __init__(self, config):
        self.config = config
        self._process = None
        self._stderr = []
        self._drain = None

    async def init(self):
        self._process = await asyncio.create_subprocess_exec(
            *subject.session(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        self._drain = asyncio.create_task(self._keep_stderr())
        await self._request({"op": "create", "config": self.config})
        return self

    async def _keep_stderr(self):
        async for line in self._process.stderr:
            self._stderr = [*self._stderr[-(STDERR_TAIL - 1) :], line.decode(errors="replace").rstrip()]

    async def _request(self, request):
        self._process.stdin.write((json.dumps(request) + "\n").encode())
        await self._process.stdin.drain()
        line = await self._process.stdout.readline()
        if not line:
            tail = "\n".join(self._stderr[-10:])
            raise RuntimeError(f"the agent exited during {request['op']}:\n{tail}")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(f"the agent refused {request['op']}: {reply['error']}")
        return reply["result"]

    async def turn(self, text):
        """The user's message, answered: how it settled, and the entries the run appended."""
        result = await self._request({"op": "turn", "text": text})
        if result.get("status") == "unanswered" and result.get("reason") not in ("turn_limit", "aborted"):
            raise TurnFailed(digest(result.get("entries"))["error"] or result.get("reason") or "the turn failed")
        return result

    async def settle(self, timeout):
        """Wait for submitted work and the runs its follow-ups start, as an open tab would."""
        return await self._request({"op": "settle", "timeout": timeout})

    async def restart(self):
        """A tab closed and opened again with nothing stored: the records Galaxy keeps attached to
        the launch history, which the caller chooses among with `recover`, as a user does."""
        return (await self._request({"op": "restart"}) or {}).get("records") or []

    async def recover(self, page_id=None):
        """Continue the session whose record is `page_id`, or start a new one."""
        await self._request({"op": "recover", "page_id": page_id})

    async def export(self):
        """The conversation as a saved session, as the page saves it."""
        return await self._request({"op": "export"})

    async def close(self):
        """Give the process back; a suite is hundreds of sessions in one interpreter."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            process.stdin.write(b'{"op": "close"}\n')
            await process.stdin.drain()
            process.stdin.close()
            await asyncio.wait_for(process.wait(), timeout=10)
        except (asyncio.TimeoutError, ConnectionError):
            process.kill()
            await process.wait()
        finally:
            if self._drain:
                self._drain.cancel()
