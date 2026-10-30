import json
import os
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from scpi_replay.cassette import Cassette
from scpi_replay.cli import main, parse_address
from scpi_replay.server import ReplayServer
from scpi_replay.proxy import Recorder

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


def start(args, **kw):
    return subprocess.Popen([sys.executable, "-m", "scpi_replay", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, **kw)


def query(port, data=b"X?\n"):
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        if data.endswith(b"?\n"):
            return s.recv(100)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_serve_subprocess(tmp_path, sig):
    cas = make(tmp_path / "c.jsonl")
    p = start(["serve", str(cas), "--port", "0"])
    try:
        line = p.stdout.readline().strip()
        assert line.startswith(f"serving {cas} on 127.0.0.1:") and line.endswith("(lookup)")
        port = int(line.split("127.0.0.1:")[1].split()[0])
        assert query(port) == b"ans:X?\n"
        p.send_signal(sig)
        out, err = p.communicate(timeout=10)
    finally:
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()
    assert p.returncode == 0
    assert "served 1 queries, 0 unknown, 0 mismatches" in err


def test_record_listen_default_and_note(tmp_path):
    with EchoInstrument() as inst:
        out = tmp_path / "r.jsonl"
        p = start(["record", "--target", f"127.0.0.1:{inst.port}", "-o", str(out), "--note", "hi"])
        try:
            line = p.stdout.readline().strip()
            assert line.startswith(f"recording 127.0.0.1:{inst.port} via 127.0.0.1:")
            assert line.endswith(f"-> {out}")
            port = int(line.split(" via ")[1].split(" ")[0].split(":")[1])
            assert port != 5025
            p.send_signal(signal.SIGINT if sys.platform != "win32" else signal.SIGTERM)
            o, e = p.communicate(timeout=10)
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()
    assert "recorded 0 connection(s)" in o
    assert Cassette.load(out).header["note"] == "hi"


def test_record_once(tmp_path):
    with EchoInstrument() as inst:
        out = tmp_path / "r.jsonl"
        p = start(["record", "--target", f"127.0.0.1:{inst.port}", "-o", str(out), "--listen", "0",
                   "--once"])
        try:
            line = p.stdout.readline().strip()
            port = int(line.split(" via ")[1].split(" ")[0].split(":")[1])
            assert query(port) == b"ans:X?\n"
            p.communicate(timeout=10)
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()
        assert p.returncode == 0
    ex = Cassette.load(out).exchanges()
    assert [e.send.text for e in ex] == ["X?"]
