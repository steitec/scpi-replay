import json
import re
import socket
import time

import pytest

from scpi_replay.server import ReplayServer

T = 5


def make(path, rows):
    """rows: (t, conn, dir, data[, extra]) -> cassette file with explicit times."""
    lines = [{"format": "scpi-replay/1", "recorded": "x", "target": "h:1", "tool": "t", "note": ""}]
    for t, conn, d, data, *extra in rows:
        obj = {"t": t, "conn": conn, "dir": d}
        obj.update(extra[0] if extra else {})
        if data is not None:
            obj["data"] = data
        lines.append(obj)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def connect(srv):
    return socket.create_connection(("127.0.0.1", srv.port), timeout=T)


def ask(s, cmd, timeout=T):
    s.settimeout(timeout)
    s.sendall(cmd + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def wait_for(cond):
    deadline = time.monotonic() + T
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


READS = [(0.1, 1, "send", "READ?"), (0.2, 1, "recv", "1"),
         (0.3, 1, "send", "READ?"), (0.4, 1, "recv", "2"),
         (0.5, 1, "send", "READ?"), (0.6, 1, "recv", "3")]


def test_lookup_repeats_then_last(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS)) as srv, connect(srv) as s:
        assert [ask(s, b"READ?") for _ in range(4)] == [b"1\n", b"2\n", b"3\n", b"3\n"]
        assert srv.served == 4 and srv.problems == []
        assert srv.resource == f"TCPIP::127.0.0.1::{srv.port}::SOCKET"
        assert srv.unreached() == []


def test_lookup_matches_normalised(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS)) as srv, connect(srv) as s:
        assert ask(s, b"read?") == b"1\n"


def test_lookup_unknown_query(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS)) as srv, connect(srv) as s:
        with pytest.raises(socket.timeout):
            ask(s, b"FOO?", timeout=0.3)
        assert srv.problems == ["unknown query: FOO?"]
        assert ask(s, b"READ?") == b"1\n"


def test_lookup_non_query_silent(tmp_path):
    rows = [(0.1, 1, "send", "OUTP ON")] + READS
    with ReplayServer(make(tmp_path / "c", rows)) as srv, connect(srv) as s:
        s.sendall(b"OUTP ON\n")
        s.sendall(b"WHATEVER 5\n")
        assert ask(s, b"READ?") == b"1\n"
        assert srv.problems == []


def test_lookup_no_response_query_known(tmp_path):
    rows = [(0.1, 1, "send", "ERR?")]
    with ReplayServer(make(tmp_path / "c", rows)) as srv, connect(srv) as s:
        with pytest.raises(socket.timeout):
            ask(s, b"ERR?", timeout=0.3)
        assert srv.problems == []


def test_lookup_binary_response_exact(tmp_path):
    rows = [(0.1, 1, "send", "CURV?"), (0.2, 1, "recv", None, {"b64": "IzE0YWIKY2Q=", "binary": True, "term": "\r\n"})]
    with ReplayServer(make(tmp_path / "c", rows)) as srv, connect(srv) as s:
        s.settimeout(T)
        s.sendall(b"CURV?\n")
        buf = b""
        while not buf.endswith(b"cd\r\n"):
            buf += s.recv(100)
        assert buf == b"#14ab\ncd\r\n"


def test_strict_in_order(tmp_path):
    rows = [(0.1, 1, "send", "OUTP ON"), (0.2, 1, "send", "READ?"), (0.3, 1, "recv", "7")]
    with ReplayServer(make(tmp_path / "c", rows), mode="strict") as srv, connect(srv) as s:
        s.sendall(b"outp ON\n")
        assert ask(s, b"READ?") == b"7\n"
        assert srv.unreached() == [] and srv.problems == []


def test_strict_mismatch_closes(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS), mode="strict") as srv, connect(srv) as s:
        s.settimeout(T)
        s.sendall(b"MEAS?\n")
        assert s.recv(100) == b""
        assert srv.problems == ["expected READ?, got MEAS?"]


def test_strict_extra_command(tmp_path):
    rows = [(0.1, 1, "send", "READ?"), (0.2, 1, "recv", "1")]
    with ReplayServer(make(tmp_path / "c", rows), mode="strict") as srv, connect(srv) as s:
        assert ask(s, b"READ?") == b"1\n"
        s.sendall(b"READ?\n")
        assert s.recv(100) == b""
        assert srv.problems == ["expected end of recording, got READ?"]


def test_strict_unreached(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS), mode="strict") as srv, connect(srv) as s:
        assert ask(s, b"READ?") == b"1\n"
        wait_for(lambda: len(srv.unreached()) == 2)
        assert [e.index for e in srv.unreached()] == [2, 3]


def test_lookup_unreached_empty(tmp_path):
    with ReplayServer(make(tmp_path / "c", READS)) as srv, connect(srv) as s:
        ask(s, b"READ?")
        assert srv.unreached() == []


def test_new_client_starts_over(tmp_path):
    for mode in ("lookup", "strict"):
        with ReplayServer(make(tmp_path / "c", READS), mode=mode) as srv:
            for _ in range(2):
                with connect(srv) as s:
                    assert ask(s, b"READ?") == b"1\n"
                    assert ask(s, b"READ?") == b"2\n"


def test_conn_selection(tmp_path):
    rows = [(0.1, 1, "send", "A?"), (0.2, 1, "recv", "one"), (0.1, 2, "send", "A?"), (0.2, 2, "recv", "two")]
    with ReplayServer(make(tmp_path / "c", rows), conn=2) as srv, connect(srv) as s:
        assert ask(s, b"A?") == b"two\n"


def test_accepts_path_string_and_cassette(tmp_path):
    from scpi_replay.cassette import Cassette
    p = make(tmp_path / "c", READS)
    with ReplayServer(str(p)) as srv, connect(srv) as s:
        assert ask(s, b"READ?") == b"1\n"
    with ReplayServer(Cassette.load(p)) as srv, connect(srv) as s:
        assert ask(s, b"READ?") == b"1\n"


def test_timing_recorded_and_cap(tmp_path):
    p = make(tmp_path / "c", [(1.0, 1, "send", "READ?"), (1.2, 1, "recv", "1")])
    with ReplayServer(p, timing="recorded") as srv, connect(srv) as s:
        t0 = time.monotonic()
        assert ask(s, b"READ?") == b"1\n"
        assert time.monotonic() - t0 >= 0.18
    p = make(tmp_path / "d", [(1.0, 1, "send", "READ?"), (2.0, 1, "recv", "1")])
    with ReplayServer(p, timing="recorded", max_delay=0.05) as srv, connect(srv) as s:
        t0 = time.monotonic()
        assert ask(s, b"READ?") == b"1\n"
        assert 0.04 <= time.monotonic() - t0 < 0.6


def test_unknown_conn_raises(tmp_path):
    from scpi_replay.cassette import Cassette
    p = make(tmp_path / "c", READS)
    with pytest.raises(ValueError, match=re.escape(f"no connection 9 in {p} (has: 1)")):
        ReplayServer(p, conn=9)
    with pytest.raises(ValueError, match=r"no connection 9 in <memory>"):
        ReplayServer(Cassette.load(p), conn=9)


def test_problems_are_logged_as_they_happen(tmp_path):
    seen = []
    with ReplayServer(make(tmp_path / "c", READS), log=seen.append) as srv, connect(srv) as s:
        s.sendall(b"NOPE?\n")
        assert ask(s, b"READ?") == b"1\n"  # NOPE? was handled before READ?
        assert seen == ["unknown query: NOPE?"]
    with ReplayServer(make(tmp_path / "c", READS), mode="strict", log=seen.append) as srv:
        with connect(srv) as s:
            s.sendall(b"WRONG\n")
            assert s.recv(10) == b""
    assert seen[1:] == ["expected READ?, got WRON"]  # normalised to the short form
