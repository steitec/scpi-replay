"""Background asyncio TCP server base (adapted from scpi-sim's transport)."""

from __future__ import annotations

import asyncio
import socket
import threading


class TcpServer:
    """A TCP server on its own asyncio loop in a background thread.

    Subclasses implement ``_serve_client``.
    """

    def __init__(self, host: str, port: int, name: str):
        self.host = host
        self.port = port
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._servers: list[asyncio.base_events.Server] = []
        self._clients: set[asyncio.Task] = set()

    def start(self) -> int:
        """Bind and start serving; return the bound port. Raises OSError if the port is taken."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name=self._name)
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._open(), self._loop).result()
        except BaseException:
            self._halt_loop()
            raise
        return self.port

    def stop(self) -> None:
        """Close the listening socket and all client connections."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._close(), self._loop).result(timeout=5)
        self._halt_loop()

    def __enter__(self) -> TcpServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- internals (run inside the server thread) ---------------------------------

    def _halt_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()
        self._loop = self._thread = None

    async def _open(self) -> None:
        # Bind every address the host resolves to ("localhost" -> ::1 and 127.0.0.1)
        # one by one, so that with port 0 all of them share the first free port.
        infos = await asyncio.get_running_loop().getaddrinfo(
            self.host, self.port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
        port = self.port
        try:
            for address in addresses:
                server = await asyncio.start_server(self._accept, address, port)
                port = server.sockets[0].getsockname()[1]
                self._servers.append(server)
        except BaseException:
            await self._close()
            raise
        self.port = port

    async def _close(self) -> None:
        for server in self._servers:
            server.close()
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        for server in self._servers:
            await server.wait_closed()
        self._servers.clear()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            await self._serve_client(reader, writer)
        except (OSError, asyncio.CancelledError):  # OSError: resets look different on Windows
            pass
        finally:
            self._clients.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def _serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raise NotImplementedError
