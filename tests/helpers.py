"""Shared test helpers."""

from __future__ import annotations

import asyncio

from scpi_replay._server import TcpServer


class EchoInstrument(TcpServer):
    """Tiny instrument: ``X?`` -> ``ans:X?``, ``BIN?`` -> block ``#14ab\\ncd``, ``BYE`` closes."""

    def __init__(self) -> None:
        super().__init__("127.0.0.1", 0, name="echo-instrument")
        self.received = bytearray()

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            self.received += line
            cmd = line.strip()
            if cmd == b"BYE":
                return
            if cmd == b"BIN?":
                writer.write(b"#14ab\ncd\n")
            elif cmd.endswith(b"?"):
                writer.write(b"ans:" + cmd + b"\n")
            await writer.drain()
