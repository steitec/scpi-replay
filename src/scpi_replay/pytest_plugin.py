"""pytest plugin: run a test against a recorded cassette.

Fixture ``scpi_cassette`` -- ``factory(path, *, mode="lookup", conn=None,
require_all=True, target=None)`` starts a :class:`ReplayServer` on a free
port and returns it (use ``server.resource``). Relative paths are relative to
the rootdir. At teardown the test fails if the server saw unknown queries or
mismatches, or (strict mode, ``require_all``) if recorded exchanges were never
reached.

With ``--scpi-record`` the factory records instead: it returns a
:class:`Recorder` that proxies ``target=`` (or ``--scpi-target HOST[:PORT]``)
and writes the cassette to ``path``. Recording truncates ``path``, so give
every test its own cassette.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .cli import split_address
from .proxy import Recorder
from .server import ReplayServer


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("scpi-replay")
    group.addoption("--scpi-record", action="store_true",
                    help="scpi_cassette records through a proxy instead of replaying")
    group.addoption("--scpi-target", metavar="HOST[:PORT]",
                    help="instrument to record from (default port 5025)")


class CassettePool:
    """Servers started for one test; all of them are stopped at the end."""

    def __init__(self, config: pytest.Config):
        self.config = config
        self.root = config.rootpath
        self.record = bool(config.getoption("--scpi-record"))
        self.servers: list[tuple[ReplayServer | Recorder, bool]] = []

    def _target(self, target) -> tuple[str, int]:
        if target is None:
            target = self.config.getoption("--scpi-target")
        if target is None:
            pytest.fail("scpi-replay: --scpi-record needs a target: pass target=... or "
                        "--scpi-target HOST[:PORT]", pytrace=False)
        if isinstance(target, str):
            try:
                return split_address(target)
            except ValueError as e:
                pytest.fail(f"scpi-replay target: {e}", pytrace=False)
        return target

    def start(self, path, *, mode="lookup", conn=None, require_all=True, target=None):
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        try:
            if self.record:
                address = self._target(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                server = Recorder(address, path)
                check = False
            else:
                server = ReplayServer(path, mode=mode, conn=conn)
                check = require_all
            server.start()
        except (ValueError, OSError) as e:  # ValueError: bad cassette or unknown conn
            pytest.fail(f"scpi-replay: {e}", pytrace=False)
        self.servers.append((server, check))
        return server

    def finish(self) -> None:
        """Stop every server, then fail the test if a replay server found problems."""
        servers, self.servers = self.servers, []
        errors: list[BaseException] = []
        for server, _ in servers:
            try:
                server.stop()
            except Exception as e:  # stop the others anyway
                errors.append(e)
        if errors:
            raise errors[0]
        lines: list[str] = []
        for server, require_all in servers:
            if not isinstance(server, ReplayServer):
                continue
            lines += server.problems
            if require_all:
                lines += [f"never reached: {ex.index:03d} {ex.send.text}" for ex in server.unreached()]
        if lines:
            pytest.fail("scpi_cassette:\n  " + "\n  ".join(lines), pytrace=False)


@pytest.fixture
def scpi_cassette(request: pytest.FixtureRequest):
    """Factory that replays (or with ``--scpi-record`` records) a cassette for this test.

    Recording overwrites the cassette: use one cassette file per test.
    """
    pool = CassettePool(request.config)
    yield pool.start
    pool.finish()
