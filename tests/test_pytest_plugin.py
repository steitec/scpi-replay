"""The scpi_cassette fixture, exercised in inner pytest runs."""

from __future__ import annotations

import json
import textwrap

import pytest

from helpers import EchoInstrument

HEADER = {"format": "scpi-replay/1", "recorded": "2026-10-30T10:00:00+01:00",
          "target": "x:5025", "tool": "t", "note": ""}
CASSETTE = [HEADER,
            {"t": 0.0, "conn": 1, "dir": "send", "data": "A?"},
            {"t": 0.01, "conn": 1, "dir": "recv", "data": "one"},
            {"t": 0.02, "conn": 1, "dir": "send", "data": "B?"},
            {"t": 0.03, "conn": 1, "dir": "recv", "data": "two"}]

QUERY = textwrap.dedent('''
    import socket

    def ask(resource, text):
        port = int(resource.split("::")[2])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall(text.encode() + b"\\n")
            return s.makefile("rb").readline().strip().decode()
''')


@pytest.fixture
def bench(pytester):
    pytester.makefile(".scpi.jsonl", c="\n".join(json.dumps(r) for r in CASSETTE) + "\n")
    return pytester


def run(pytester, body: str, *args):
    pytester.makepyfile(test_inner=QUERY + textwrap.dedent(body))
    return pytester.runpytest_subprocess(*args)


def test_replays_cassette(bench):
    result = run(bench, '''
        def test_it(scpi_cassette):
            server = scpi_cassette("c.scpi.jsonl")
            assert server.resource.startswith("TCPIP::127.0.0.1::")
            assert ask(server.resource, "A?") == "one"
    ''')
    result.assert_outcomes(passed=1)


def test_unknown_query_fails_teardown(bench):
    result = run(bench, '''
        def test_it(scpi_cassette):
            server = scpi_cassette("c.scpi.jsonl")
            server.resource
            import socket
            with socket.create_connection(("127.0.0.1", server.port)) as s:
                s.sendall(b"NOPE?\\n")
                s.settimeout(0.5)
                try:
                    s.recv(10)
                except OSError:
                    pass
    ''')
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*unknown query*NOPE?*"])


def test_unreached_exchanges_fail_in_strict(bench):
    body = '''
        def test_it(scpi_cassette):
            server = scpi_cassette("c.scpi.jsonl", mode="strict"%s)
            assert ask(server.resource, "A?") == "one"
    '''
    result = run(bench, body % "")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*never reached: 002 B?*"])
    result = run(bench, body % ", require_all=False")
    result.assert_outcomes(passed=1)


def test_record_through_proxy(bench):
    with EchoInstrument() as echo:
        result = run(bench, '''
            def test_it(scpi_cassette):
                rec = scpi_cassette("new/dir/r.scpi.jsonl")
                assert rec.resource.startswith("TCPIP::127.0.0.1::")
                assert ask(rec.resource, "X?") == "ans:X?"
        ''', "--scpi-record", f"--scpi-target=127.0.0.1:{echo.port}")
    result.assert_outcomes(passed=1)
    text = (bench.path / "new/dir/r.scpi.jsonl").read_text()
    assert '"data": "X?"' in text


def test_record_without_target_is_usage_error(bench):
    result = run(bench, '''
        def test_it(scpi_cassette):
            scpi_cassette("r.scpi.jsonl")
    ''', "--scpi-record")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*--scpi-target*"])
    assert not (bench.path / "r.scpi.jsonl").exists()
