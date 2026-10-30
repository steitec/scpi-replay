"""Recording proxy: forwards a raw SCPI socket unchanged and logs it to a cassette."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Callable

from ._server import TcpServer
from .cassette import CassetteWriter
from .framing import Framer

CONNECT_TIMEOUT = 10.0  # seconds to reach the target


class _Conn:
    """Recording state of one proxied connection; logging failures never propagate."""

    def __init__(self, cassette: CassetteWriter, number: int) -> None:
        self.cassette = cassette
        self.number = number
        self.framers = {"send": Framer(), "recv": Framer()}
        self.broken = False
        self.closed_by: str | None = None

    def guard(self, call, *args, **kwargs) -> None:
        """Run a cassette/framer call; after the first failure stop recording this connection."""
        if self.broken:
            return
        try:
            call(*args, **kwargs)
        except Exception as exc:
            self.broken = True
            print(f"scpi-replay: recording of connection {self.number} stopped: {exc!r}",
                  file=sys.stderr)

    def message(self, direction: str, chunk: bytes) -> None:
        self.guard(lambda: [self.cassette.message(self.number, direction, f)
                            for f in self.framers[direction].feed(chunk)])

    def event(self, kind: str, **kw) -> None:
        self.guard(self.cassette.event, self.number, kind, **kw)

    def finish(self) -> None:
        """Flush leftovers of both directions, then write the close event."""
        def flush() -> None:
            for direction, framer in self.framers.items():
                rest = framer.flush()
                if rest is not None:
                    self.cassette.message(self.number, direction, rest)
                    self.cassette.event(self.number, "warning", text="incomplete message at close")
        self.guard(flush)
        self.event("close", by=self.closed_by or "recorder")


class Recorder(TcpServer):
    """Listen for clients, forward each to ``target`` and record the traffic.

    ``log`` (optional) is called with the text of each warning as it is
    recorded, for example when the target refuses a connection.
    ``connections`` counts clients, ``opened`` the connections to the target.
    """

    def __init__(self, target: tuple[str, int], path: str | Path, *, host: str = "127.0.0.1",
                 port: int = 0, note: str = "", once: bool = False,
                 log: Callable[[str], None] | None = None):
        super().__init__(host, port, name="scpi-replay-record")
        self.target = target
        self.once = once
        self.log = log
        self.connections = 0
        self.opened = 0
        self.finished = threading.Event()
        self._path = path
        self._note = note
        self._cassette: CassetteWriter | None = None

    @property
    def resource(self) -> str:
        return f"TCPIP::{self.host}::{self.port}::SOCKET"

    async def _open(self) -> None:
        await super()._open()
        try:  # only after a successful bind, so a failed start leaves the file alone
            self._cassette = CassetteWriter(
                self._path, target=f"{self.target[0]}:{self.target[1]}", note=self._note)
        except BaseException:
            await self._close()
            raise

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            if self._cassette is not None:
                self._cassette.close()

    def _warn(self, conn: _Conn, text: str) -> None:
        conn.event("warning", text=text)
        if self.log is not None:
            self.log(text)

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        conn = _Conn(self._cassette, self.connections)
        t_writer = None
        try:
            try:
                t_reader, t_writer = await asyncio.wait_for(
                    asyncio.open_connection(*self.target), CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                self._warn(conn, f"connection to target timed out after {CONNECT_TIMEOUT:g}s")
                return
            except OSError as exc:
                kind = "refused" if isinstance(exc, ConnectionRefusedError) else "failed"
                self._warn(conn, f"connection to target {kind}: {exc}")
                return
            self.opened += 1
            conn.event("open")

            async def pump(src, dst, direction: str, side: str) -> None:
                try:
                    while chunk := await src.read(65536):
                        dst.write(chunk)  # forward first
                        await dst.drain()
                        conn.message(direction, chunk)
                except OSError:
                    pass
                if conn.closed_by is None:
                    conn.closed_by = side
                # EOF on one side ends the connection: unblock the other pump
                writer.close()
                t_writer.close()

            await asyncio.gather(pump(reader, t_writer, "send", "client"),
                                 pump(t_reader, writer, "recv", "target"))
        finally:
            if t_writer is not None:  # also reached on cancellation (stop)
                conn.finish()
                t_writer.close()
                try:
                    await t_writer.wait_closed()
                except (OSError, asyncio.CancelledError):
                    pass
            if self.once:
                self.finished.set()
