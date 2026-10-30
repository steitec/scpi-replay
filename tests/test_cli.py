import json
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from scpi_replay.cassette import Cassette
from scpi_replay import cli
from scpi_replay.cli import main, parse_address
from scpi_replay.server import ReplayServer

from helpers import EchoInstrument
from test_show import make as make_show


def make(path, answer="ans:X?"):
    rows = [{"format": "scpi-replay/1", "recorded": "x", "target": "h:1", "tool": "t", "note": ""},
            {"t": 0.0, "conn": 1, "dir": "send", "data": "X?"},
            {"t": 0.01, "conn": 1, "dir": "recv", "data": answer}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == "scpi-replay 0.1.0"


def test_parse_address():
    assert parse_address("10.0.0.5") == ("10.0.0.5", 5025)
    assert parse_address("10.0.0.5:6") == ("10.0.0.5", 6)
    assert parse_address("[::1]:6000") == ("::1", 6000)
    with pytest.raises(SystemExit) as e:
        parse_address("host:x")
    assert e.value.code == 2


@pytest.mark.parametrize("opt", ["--timeout", "--max-gap"])
def test_nonpositive_option(tmp_path, opt):
    with pytest.raises(SystemExit) as e:
        main(["check", str(make(tmp_path / "c.jsonl")), "--target", "127.0.0.1:1", opt, "0"])
    assert e.value.code == 2


def test_show(tmp_path, capsys):
    assert main(["show", str(make_show(tmp_path)), "--times"]) == 0
    assert "> *IDN?" in capsys.readouterr().out
    assert main(["show", str(tmp_path / "missing")]) == 2


def test_check_pass_fail_and_outputs(tmp_path, capsys):
    cas = make(tmp_path / "c.jsonl")
    with ReplayServer(cas) as srv:
        t = f"127.0.0.1:{srv.port}"
        junit, js = tmp_path / "o.xml", tmp_path / "o.json"
        assert main(["check", str(cas), "--target", t, "--timing", "fast",
                     "--junit", str(junit), "--json", str(js)]) == 0
        ET.parse(junit)
        json.loads(js.read_text())
    bad = make(tmp_path / "d.jsonl", answer="other")
    capsys.readouterr()
    with ReplayServer(cas) as srv:
        assert main(["check", str(bad), "--target", f"127.0.0.1:{srv.port}", "--timing", "fast"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_errors(tmp_path, capsys):
    cas = make(tmp_path / "c.jsonl")
    assert main(["check", str(cas), "--target", "127.0.0.1:1", "--timing", "fast"]) == 2
    assert "scpi-replay: error:" in capsys.readouterr().err
    assert main(["check", str(tmp_path / "nope"), "--target", "127.0.0.1:1"]) == 2
    rules = tmp_path / "r.toml"
    rules.write_text("[[rule]]\ncompare = 'bogus'\nmatch='X'\n")
    with ReplayServer(cas) as srv:
        assert main(["check", str(cas), "--target", f"127.0.0.1:{srv.port}",
                     "--rules", str(rules), "--timing", "fast"]) == 2


POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stop summary needs a graceful signal; Windows send_signal is TerminateProcess")


class Child:
    """A scpi-replay subprocess; the first stdout line is read with a deadline."""

    def __init__(self, *args):
        self.p = subprocess.Popen([sys.executable, "-m", "scpi_replay", *map(str, args)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: self._q.put(self.p.stdout.readline()), daemon=True).start()

    def first_line(self, timeout=10.0) -> str:
        try:
            line = self._q.get(timeout=timeout).strip()
        except queue.Empty:
            line = ""
        if not line:
            self.p.kill()
            _, err = self.p.communicate()
            pytest.fail(f"no status line from child (rc={self.p.returncode}); stderr:\n{err}")
        return line

    def finish(self, sig=None, timeout=10.0):
        if sig is not None:
            self.p.send_signal(sig)
        return self.p.communicate(timeout=timeout)

    def close(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait()
        self.p.stdout.close()
        self.p.stderr.close()


def port_of(line, marker):
    return int(line.split(marker)[1].split()[0].split(":")[-1])


def query(port, data=b"X?\n"):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        if data.endswith(b"?\n"):
            return s.recv(100)


@POSIX_ONLY
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_serve_subprocess(tmp_path, sig):
    cas = make(tmp_path / "c.jsonl")
    c = Child("serve", cas, "--port", "0")
    try:
        line = c.first_line()
        assert line.startswith(f"serving {cas} on 127.0.0.1:") and line.endswith("(lookup)")
        assert query(port_of(line, " on ")) == b"ans:X?\n"
        _, err = c.finish(sig)
    finally:
        c.close()
    assert c.p.returncode == 0
    assert "served 1 queries, 0 unknown, 0 mismatches" in err


@POSIX_ONLY
def test_serve_unknown_query_exits_1(tmp_path):
    c = Child("serve", make(tmp_path / "c.jsonl"), "--port", "0")
    try:
        port = port_of(c.first_line(), " on ")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"NOPE?\nX?\n")
            assert s.recv(100) == b"ans:X?\n"
        _, err = c.finish(signal.SIGINT)
    finally:
        c.close()
    assert c.p.returncode == 1
    assert "served 1 queries, 1 unknown, 0 mismatches" in err
    assert "unknown query: NOPE?" in err


@POSIX_ONLY
def test_serve_strict_summary(tmp_path):
    c = Child("serve", make(tmp_path / "c.jsonl"), "--port", "0", "--mode", "strict")
    try:
        line = c.first_line()
        assert line.endswith("(strict)")
        query(port_of(line, " on "), b"WRONG\n")
        time.sleep(0.3)  # let the server register the mismatch before the stop
        _, err = c.finish(signal.SIGINT)
    finally:
        c.close()
    assert c.p.returncode == 1
    assert "0 queries, 0 unknown, 1 mismatches, 1 recorded exchanges never reached" in err
    assert "expected X?, got " in err


@POSIX_ONLY
def test_record_listen_default_and_note(tmp_path):
    out = tmp_path / "r.jsonl"
    with EchoInstrument() as inst:
        c = Child("record", "--target", f"127.0.0.1:{inst.port}", "-o", out, "--note", "hi")
        try:
            line = c.first_line()
            assert line.startswith(f"recording 127.0.0.1:{inst.port} via 127.0.0.1:")
            assert line.endswith(f"-> {out}")
            assert port_of(line, " via ") != 5025
            o, _ = c.finish(signal.SIGINT)
        finally:
            c.close()
    assert "recorded 0 connection(s)" in o
    assert Cassette.load(out).header["note"] == "hi"


def test_record_once(tmp_path):
    out = tmp_path / "r.jsonl"
    with EchoInstrument() as inst:
        c = Child("record", "--target", f"127.0.0.1:{inst.port}", "-o", out, "--listen", "0",
                  "--once", "--note", "n")
        try:
            assert query(port_of(c.first_line(), " via ")) == b"ans:X?\n"
            c.finish()
        finally:
            c.close()
        assert c.p.returncode == 0
    cas = Cassette.load(out)
    assert [e.send.text for e in cas.exchanges()] == ["X?"]
    assert cas.header["note"] == "n"


def test_record_busy_port_keeps_file(tmp_path, capsys):
    out = tmp_path / "r.jsonl"
    out.write_text("precious")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        rc = main(["record", "--target", "127.0.0.1:1", "-o", str(out),
                   "--listen", str(busy.getsockname()[1])])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("scpi-replay: error: cannot listen on") and err.count("\n") == 1
    assert out.read_text() == "precious"


def test_record_unwritable_output(tmp_path, capsys):
    rc = main(["record", "--target", "127.0.0.1:1", "-o", str(tmp_path / "no" / "x.jsonl"),
               "--listen", "0"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot write" in err and "cannot listen" not in err


@pytest.mark.parametrize("argv", [
    ["serve", "F", "--port", "70000"], ["serve", "F", "--port", "-1"],
    ["serve", "F", "--conn", "0"], ["check", "F", "--target", "h", "--conn", "0"],
    ["record", "--target", "h", "-o", "x", "--listen", "70000"]])
def test_range_errors(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2


class FakeServer:
    seen: dict = {}

    def __init__(self, cassette, **kw):
        FakeServer.seen = {"cassette": cassette, **kw}
        self.problems, self.served = [], 0

    def start(self):
        return 1234

    def stop(self):
        pass

    def unreached(self):
        return []


def test_serve_option_wiring(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ReplayServer", FakeServer)
    monkeypatch.setattr(cli, "_wait_forever", lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    rc = main(["serve", "F", "--host", "0.0.0.0", "--port", "9", "--mode", "strict", "--conn", "2",
               "--timing", "recorded", "--max-delay", "1.5"])
    assert rc == 0
    assert callable(FakeServer.seen.pop("log"))
    assert FakeServer.seen == {"cassette": "F", "host": "0.0.0.0", "port": 9, "mode": "strict",
                               "conn": 2, "timing": "recorded", "max_delay": 1.5}
    assert "serving F on 0.0.0.0:1234 (strict)" in capsys.readouterr().out


def test_check_option_wiring(monkeypatch):
    seen = {}

    class R:
        ok = True

        def text(self, color=False):
            return "ok"

    def fake(file, target, **kw):
        seen.update(file=file, target=target, **kw)
        return R()

    monkeypatch.setattr(cli, "check", fake)
    assert main(["check", "F", "--target", "h:7", "--conn", "3", "--timing", "fast", "--max-gap", "2",
                 "--timeout", "3", "--rules", "r.toml"]) == 0
    assert seen == {"file": "F", "target": ("h", 7), "rules": "r.toml", "timing": "fast",
                    "max_gap": 2.0, "timeout": 3.0, "conn": 3}


@pytest.mark.parametrize("cmd", [["check", "--target", "127.0.0.1:1", "--timing", "fast"],
                                 ["serve", "--port", "0"], ["show"]])
def test_unknown_conn_is_usage_error(tmp_path, capsys, monkeypatch, cmd):
    monkeypatch.setattr(cli, "_wait_forever", lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    cas = make(tmp_path / "c.jsonl")
    assert main([cmd[0], str(cas), *cmd[1:], "--conn", "99"]) == 2
    assert capsys.readouterr().err == f"scpi-replay: error: no connection 99 in {cas} (has: 1)\n"


def test_record_once_refused_target_warns_and_exits_1(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    out = tmp_path / "r.jsonl"
    c = Child("record", "--target", f"127.0.0.1:{dead}", "-o", out, "--listen", "0", "--once")
    try:
        port = port_of(c.first_line(), " via ")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            assert s.recv(10) == b""
        _, err = c.finish()
    finally:
        c.close()
    assert c.p.returncode == 1
    assert err.startswith("scpi-replay: warning: connection to target refused")


def test_serve_logs_unknown_query_while_running(tmp_path):
    c = Child("serve", make(tmp_path / "c.jsonl"), "--port", "0")
    try:
        port = port_of(c.first_line(), " on ")
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: lines.put(c.p.stderr.readline()), daemon=True).start()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"FOO?\n")
            assert lines.get(timeout=10) == "scpi-replay: unknown query: FOO?\n"
    finally:
        c.close()


@POSIX_ONLY
def test_check_interrupted_prints_partial_report(tmp_path):
    cas = make(tmp_path / "c.jsonl")
    with socket.socket() as mute:  # accepts, never answers
        mute.bind(("127.0.0.1", 0))
        mute.listen()
        mute.settimeout(10)
        p = subprocess.Popen([sys.executable, "-m", "scpi_replay", "check", str(cas), "--target",
                              f"127.0.0.1:{mute.getsockname()[1]}", "--timing", "fast",
                              "--timeout", "30"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            conn, _ = mute.accept()
            with conn:
                conn.settimeout(10)
                assert conn.recv(10) == b"X?\n"  # check is now waiting for the answer
                p.send_signal(signal.SIGINT)
                out, err = p.communicate(timeout=10)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
    assert p.returncode == 1
    assert out.splitlines() == ["aborted: interrupted", "0 queries: 0 passed, 0 failed, 0 ignored"]
    assert err == ""
