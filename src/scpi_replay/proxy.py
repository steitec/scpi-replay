"""Recording proxy: forwards a raw SCPI socket unchanged and logs it to a cassette."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from ._server import TcpServer
from .cassette import CassetteWriter
from .framing import Framer


class Recorder(TcpServer):
    """Listen for clients, forward each to ``target`` and record the traffic."""

    def __init__(self, target: tuple[str, int], path: str | Path, *, host: str = "127.0.0.1",
                 port: int = 0, note: str = "", once: bool = False):
        super().__init__(host, port, name="scpi-replay-record")
        self.target = target
        self.once = once
        self.connections = 0
        self.finished = threading.Event()
        self._cassette = CassetteWriter(path, target=f"{target[0]}:{target[1]}", note=note)

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            self._cassette.close()

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        conn = self.connections
        cassette = self._cassette
        try:
            try:
                t_reader, t_writer = await asyncio.open_connection(*self.target)
            except OSError as exc:
                kind = "refused" if isinstance(exc, ConnectionRefusedError) else "failed"
                cassette.event(conn, "warning", text=f"connection to target {kind}: {exc}")
                return
            cassette.event(conn, "open")
            closer: list[str] = []

            async def pump(src, dst, direction: str, side: str) -> None:
                framer = Framer()

                def log(call, *args) -> None:
                    try:
                        call(*args)
                    except Exception as exc:  # never break forwarding
                        cassette.event(conn, "warning", text=f"recording error: {exc!r}")

                try:
                    while chunk := await src.read(65536):
                        dst.write(chunk)  # forward first
                        await dst.drain()
                        log(lambda: [cassette.message(conn, direction, f) for f in framer.feed(chunk)])
                except OSError:
                    pass
                if not closer:
                    closer.append(side)
                    cassette.event(conn, "close", by=side)
                    # EOF on one side ends the connection: unblock the other pump
                    for w in (writer, t_writer):
                        w.close()
                rest = framer.flush()
                if rest is not None:
                    log(cassette.message, conn, direction, rest)
                    cassette.event(conn, "warning", text="incomplete message at close")

            try:
                await asyncio.gather(pump(reader, t_writer, "send", "client"),
                                     pump(t_reader, writer, "recv", "target"))
            finally:
                t_writer.close()
                try:
                    await t_writer.wait_closed()
                except OSError:
                    pass
        finally:
            if self.once and not self.finished.is_set():
                self.finished.set()
