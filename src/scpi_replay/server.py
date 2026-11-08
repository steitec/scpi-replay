"""Replay server: answers on a raw TCP socket from a recorded cassette."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable, Literal

from scpi_replay._server import TcpServer
from scpi_replay.cassette import Cassette, Exchange
from scpi_replay.framing import Framer
from scpi_replay.scpi import is_query, normalize


class ReplayServer(TcpServer):
    """Stand-in for the recorded instrument.

    ``lookup``: queries are matched by normalised command; repeats get the
    recorded responses in order, then the last one again. Unknown queries get
    no answer. ``strict``: the client must send exactly the recorded commands
    in order; the first mismatch closes the connection. ``log`` (optional) is
    called with each problem (unknown query, mismatch) as it happens.
    """

    def __init__(self, cassette: Cassette | str | Path, *, host: str = "127.0.0.1", port: int = 0,
                 mode: Literal["lookup", "strict"] = "lookup", conn: int | None = None,
                 timing: Literal["fast", "recorded"] = "fast", max_delay: float = 5.0,
                 log: Callable[[str], None] | None = None):
        super().__init__(host, port, name="replay-server")
        if isinstance(cassette, Cassette):
            name = "<memory>"
        else:
            name = str(cassette)
            cassette = Cassette.load(cassette)
        cassette.require_conn(conn, name)
        self.mode = mode
        self.timing = timing
        self.max_delay = max_delay
        self.log = log
        self._exchanges = cassette.exchanges(conn)
        self._by_cmd: dict[str, list[Exchange]] = {}
        for ex in self._exchanges:
            if ex.query:
                self._by_cmd.setdefault(normalize(ex.send.text), []).append(ex)
        self._lock = threading.Lock()
        self._problems: list[str] = []
        self._served = 0
        self._furthest = 0  # strict: most exchanges any client got through

    @property
    def resource(self) -> str:
        return f"TCPIP::{self.host}::{self.port}::SOCKET"

    @property
    def problems(self) -> list[str]:
        with self._lock:
            return list(self._problems)

    @property
    def served(self) -> int:
        with self._lock:
            return self._served

    def unreached(self) -> list[Exchange]:
        """Strict mode: recorded exchanges no client ever reached (lookup: none)."""
        if self.mode != "strict":
            return []
        with self._lock:
            return self._exchanges[self._furthest:]

    def _problem(self, text: str) -> None:
        with self._lock:
            self._problems.append(text)
        if self.log is not None:
            self.log(text)

    async def _reply(self, writer: asyncio.StreamWriter, ex: Exchange) -> None:
        resp = ex.response
        if resp is None:
            return
        if self.timing == "recorded" and ex.latency:
            await asyncio.sleep(min(ex.latency, self.max_delay))
        with self._lock:  # before the write: a client that has the answer sees it counted
            self._served += 1
        writer.write(resp.raw + (resp.term or b"\n"))
        await writer.drain()

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        framer = Framer()
        counters: dict[str, int] = {}
        pos = 0
        while chunk := await reader.read(65536):
            for frame in framer.feed(chunk):
                cmd = normalize(frame.data.decode("utf-8", "replace"))
                if self.mode == "strict":
                    if pos >= len(self._exchanges):
                        self._problem(f"expected end of recording, got {cmd}")
                        return
                    ex = self._exchanges[pos]
                    expected = normalize(ex.send.text)
                    if cmd != expected:
                        self._problem(f"expected {expected}, got {cmd}")
                        return
                    pos += 1
                    with self._lock:
                        self._furthest = max(self._furthest, pos)
                    if ex.query:
                        await self._reply(writer, ex)
                    continue
                if not is_query(cmd):
                    continue
                matches = self._by_cmd.get(cmd)
                if not matches:
                    self._problem(f"unknown query: {cmd}")
                    continue
                n = counters.get(cmd, 0)
                counters[cmd] = n + 1
                await self._reply(writer, matches[min(n, len(matches) - 1)])
