import json
import socket
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from scpi_replay.cassette import Cassette
from scpi_replay.check import CheckError, check
from scpi_replay.compare import Rule, Verdict
from scpi_replay.server import ReplayServer

from helpers import EchoInstrument


def make(path, rows):
    lines = [{"format": "scpi-replay/1", "recorded": "x", "target": "h:1", "tool": "t", "note": ""}]
    for t, d, data, *extra in rows:
        obj = {"t": t, "conn": 1, "dir": d}
        obj.update(extra[0] if extra else {})
        if data is not None:
            obj["data"] = data
        lines.append(obj)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


BASE = [(0.0, "send", "*IDN?"), (0.01, "recv", "ACME,1"),
        (0.02, "send", "READ?"), (0.03, "recv", "+1.2E+01"),
        (0.04, "send", "OUTP ON"),
        (0.05, "send", "VOLT?"), (0.06, "recv", "5")]


def tgt(srv):
    return ("127.0.0.1", srv.port)


def run(path, srv, **kw):
    kw.setdefault("timing", "fast")
    kw.setdefault("timeout", 2.0)
    return check(path, tgt(srv), **kw)


def test_identical_target_passes(tmp_path):
    p = make(tmp_path / "c", BASE)
    with ReplayServer(p) as srv:
        rep = run(p, srv)
    assert rep.ok and rep.counts == {"PASS": 3, "FAIL": 0, "IGNORED": 0}
    assert rep.cassette == str(p) and rep.target == f"127.0.0.1:{srv.port}"


def test_memory_cassette_name(tmp_path):
    p = make(tmp_path / "c", BASE)
    with ReplayServer(p) as srv:
        assert run(Cassette.load(p), srv).cassette == "<memory>"


def test_different_values_fail(tmp_path):
    p = make(tmp_path / "c", BASE)
    other = make(tmp_path / "o", [r if r[2] != "+1.2E+01" else (r[0], r[1], "+1.3E+01") for r in BASE])
    with ReplayServer(other) as srv:
        rep = run(p, srv)
    assert not rep.ok
    bad = [r for r in rep.results if r.verdict.status == "FAIL"]
    assert len(bad) == 1 and bad[0].exchange.index == 2
    assert bad[0].actual == b"+1.3E+01" and bad[0].exchange.response.text == "+1.2E+01"
    assert "expected +1.2E+01, got +1.3E+01" in bad[0].verdict.reason


class Silent:
    """Accepts, reads, never answers; optionally closes after N lines."""

    def __init__(self, close_after=None):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen()
        self.port = self.s.getsockname()[1]
        self.close_after = close_after
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        c, _ = self.s.accept()
        with c:
            n = 0
            while d := c.recv(4096):
                n += d.count(b"\n")
                if self.close_after is not None and n >= self.close_after:
                    return

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.s.close()
        self.t.join(5)


def test_no_response_continues(tmp_path):
    p = make(tmp_path / "c", BASE)
    with Silent() as srv:
        rep = check(p, tgt(srv), timing="fast", timeout=0.2)
    assert [r.verdict.reason for r in rep.results] == ["no response"] * 3
    assert not rep.ok and rep.aborted is None


def test_connection_lost_aborts(tmp_path):
    p = make(tmp_path / "c", BASE)
    with Silent(close_after=2) as srv:
        rep = check(p, tgt(srv), timing="fast", timeout=0.3)
    assert rep.aborted and "002 READ?" in rep.aborted
    assert rep.results[0].verdict.reason == "no response"
    assert [r.verdict.reason for r in rep.results[1:]] == ["connection lost before response",
                                                           "not sent: connection lost"]
    assert len(rep.results) == 3 and not rep.ok
    assert rep.text().splitlines()[-2].startswith("aborted: ")


def test_closed_port_raises():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(CheckError, match="cannot connect to 127.0.0.1"):
        check(Cassette({}, []), ("127.0.0.1", port))


GAP = [(0.0, "send", "X?"), (0.01, "recv", "a"), (0.31, "send", "X?"), (0.32, "recv", "a")]


def timed(tmp_path, **kw):
    p = make(tmp_path / "c", GAP)
    with ReplayServer(p) as srv:
        t0 = time.monotonic()
        check(p, tgt(srv), timeout=2.0, **kw)
        return time.monotonic() - t0


def test_timing_recorded(tmp_path):
    assert timed(tmp_path, timing="recorded") >= 0.28


def test_timing_max_gap_caps(tmp_path):
    assert timed(tmp_path, timing="recorded", max_gap=0.05) < 0.25


def test_timing_fast(tmp_path):
    assert timed(tmp_path, timing="fast") < 0.25


def test_binary_send_byte_exact(tmp_path):
    p = tmp_path / "c"
    p.write_text(json.dumps({"format": "scpi-replay/1"}) + "\n"
                 + json.dumps({"t": 0, "conn": 1, "dir": "send", "b64": "REFUQSAjMTNhYg0KY2Q=", "binary": True}) + "\n"
                 + json.dumps({"t": 1, "conn": 1, "dir": "send", "data": "BIN?"}) + "\n"
                 + json.dumps({"t": 2, "conn": 1, "dir": "recv", "b64": "IzE0YWIKY2Q=", "binary": True}) + "\n")
    c = Cassette.load(p)
    sent = c.exchanges()[0].send
    assert sent.binary
    with EchoInstrument() as inst:
        rep = check(c, ("127.0.0.1", inst.port), timing="fast", timeout=2.0)
        deadline = time.monotonic() + 2
        while b"BIN?" not in inst.received and time.monotonic() < deadline:
            time.sleep(0.01)
    assert bytes(inst.received) == sent.raw + b"\n" + b"BIN?\n"
    assert rep.ok, rep.text()


def three(tmp_path):
    p = make(tmp_path / "c", BASE)
    other = make(tmp_path / "o", [r if r[2] != "+1.2E+01" else (r[0], r[1], "+1.3E+01") for r in BASE])
    with ReplayServer(other) as srv:
        return run(p, srv)


def test_text(tmp_path):
    lines = three(tmp_path).text().splitlines()
    assert lines[0] == "FAIL  002 READ?  expected +1.2E+01, got +1.3E+01"
    assert lines[1] == "PASS  001 *IDN?"
    assert lines[-1] == "3 queries: 2 passed, 1 failed, 0 ignored"
    assert "\x1b[" not in "\n".join(lines)
    assert "\x1b[31mFAIL" in three(tmp_path).text(color=True)


def test_junit(tmp_path):
    rep = three(tmp_path)
    rep.results[0].verdict = Verdict("IGNORED")
    root = ET.fromstring(rep.to_junit())
    assert root.tag == "testsuite" and root.get("tests") == "3" and root.get("failures") == "1"
    assert root.get("skipped") == "1"
    cases = {c.get("name"): c for c in root.findall("testcase")}
    assert set(cases) == {"001 *IDN?", "002 READ?", "004 VOLT?"}
    assert cases["002 READ?"].find("failure") is not None
    assert "+1.3E+01" in cases["002 READ?"].find("failure").text
    assert cases["001 *IDN?"].find("skipped") is not None


def test_json(tmp_path):
    d = json.loads(three(tmp_path).to_json())
    assert d["ok"] is False and d["aborted"] is None
    assert d["counts"] == {"PASS": 2, "FAIL": 1, "IGNORED": 0}
    assert [r["status"] for r in d["results"]] == ["PASS", "FAIL", "PASS"]
    assert d["results"][1]["expected"] == "+1.2E+01" and d["results"][1]["actual"] == "+1.3E+01"


def test_rules_ignore(tmp_path):
    three(tmp_path)  # creates the cassettes
    p = tmp_path / "c"
    other = tmp_path / "o"
    with ReplayServer(other) as srv:
        rep = run(p, srv, rules=[Rule("READ?", compare="ignore")])
    assert rep.ok and rep.counts["IGNORED"] == 1


def test_text_ignored_separator(tmp_path):
    rep = three(tmp_path)
    rep.results[0].verdict = Verdict("IGNORED")
    lines = rep.text().splitlines()
    assert "IGNORED 001 *IDN?" in lines
    assert "FAIL  002 READ?  expected +1.2E+01, got +1.3E+01" in lines
    assert "PASS  004 VOLT?" in lines


class Scripted:
    """Answers line by line from ``answers`` = {command: (delay, reply lines)}."""

    def __init__(self, answers):
        self.answers = answers
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen()
        self.port = self.s.getsockname()[1]
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        c, _ = self.s.accept()
        with c, c.makefile("rb") as f:
            try:
                for line in f:
                    delay, replies = self.answers.get(line.strip().decode(), (0, []))
                    time.sleep(delay)
                    c.sendall(b"".join(r.encode() + b"\n" for r in replies))
            except OSError:  # the checker has gone away
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.s.close()
        self.t.join(5)


def test_late_answer_is_not_taken_for_the_next_query(tmp_path):
    # *OPC? was answered at once when recorded (wait = 0.5 s); now it takes 0.8 s.
    # The late "1" arrives during the recorded 0.6 s gap before FETC? and is discarded.
    p = make(tmp_path / "c", [(0.0, "send", "*OPC?"), (0.01, "recv", "1"),
                              (0.61, "send", "FETC?"), (0.62, "recv", "+1.0E+00")])
    with Scripted({"*OPC?": (0.8, ["1"]), "FETC?": (0, ["+1.0E+00"])}) as srv:
        rep = check(p, tgt(srv), timing="recorded", timeout=0.1)
    assert [(r.exchange.send.text, r.verdict.status, r.verdict.reason) for r in rep.results] == [
        ("*OPC?", "FAIL", "no response"), ("FETC?", "PASS", "")]
    assert rep.results[1].actual == b"+1.0E+00"


def test_extra_answer_line_is_not_taken_for_the_next_query(tmp_path):
    p = make(tmp_path / "c", [(0.0, "send", "A?"), (0.01, "recv", "a"),
                              (0.2, "send", "B?"), (0.21, "recv", "b")])
    with Scripted({"A?": (0, ["a", "stray"]), "B?": (0.05, ["b"])}) as srv:
        rep = check(p, tgt(srv), timing="recorded", timeout=2.0)
    assert [r.verdict.status for r in rep.results] == ["PASS", "PASS"], rep.text()
    assert rep.results[1].actual == b"b"


def test_recorded_latency_longer_than_timeout_is_waited_for(tmp_path):
    p = make(tmp_path / "c", [(0.0, "send", "*OPC?"), (0.4, "recv", "1"),
                              (0.5, "send", "FETC?"), (0.51, "recv", "+1.0E+00")])
    with Scripted({"*OPC?": (0.4, ["1"]), "FETC?": (0, ["+1.0E+00"])}) as srv:
        rep = check(p, tgt(srv), timing="fast", timeout=0.1)
    assert rep.ok, rep.text()
    assert [r.actual for r in rep.results] == [b"1", b"+1.0E+00"]


def test_unknown_conn_raises_before_connecting(tmp_path):
    p = make(tmp_path / "c", BASE)
    with pytest.raises(ValueError, match=rf"no connection 99 in {p} \(has: 1\)"):
        check(p, ("127.0.0.1", 1), conn=99)


def test_interrupt_keeps_partial_report(tmp_path, monkeypatch):
    import sys
    check_mod = sys.modules["scpi_replay.check"]
    from scpi_replay.check import CheckInterrupted
    real = check_mod._Link.read
    calls = []

    def read(self, timeout):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(self, timeout)

    monkeypatch.setattr(check_mod._Link, "read", read)
    p = make(tmp_path / "c", BASE)
    with ReplayServer(p) as srv:
        with pytest.raises(CheckInterrupted) as e:
            run(p, srv)
    assert isinstance(e.value, KeyboardInterrupt)
    rep = e.value.report
    assert rep.aborted == "interrupted" and not rep.ok
    assert [r.exchange.send.text for r in rep.results] == ["*IDN?"]
