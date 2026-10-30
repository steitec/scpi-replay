"""End-to-end: record, serve and check a simulated DMM (scpi-sim) through PyVISA-py."""

from __future__ import annotations

import time

import pytest

pyvisa = pytest.importorskip("pyvisa")
scpi_sim = pytest.importorskip("scpi_sim")

from scpi_sim import InstrumentServer, Multimeter  # noqa: E402

from scpi_replay import Cassette, Recorder, ReplayServer, check  # noqa: E402
from scpi_replay.cli import main  # noqa: E402
from scpi_replay.compare import Rule  # noqa: E402

T = 5


@pytest.fixture
def dmm():
    with InstrumentServer(Multimeter(), port=0) as srv:
        yield srv


def run_script(port: int) -> list[str]:
    """The same PyVISA session against any raw-socket endpoint; returns the answers."""
    rm = pyvisa.ResourceManager("@py")
    try:
        inst = rm.open_resource(f"TCPIP::127.0.0.1::{port}::SOCKET",
                                read_termination="\n", write_termination="\n", timeout=T * 1000)
        try:
            answers = [inst.query("*IDN?")]
            inst.write("CONF:VOLT:DC 10")
            answers += [inst.query("READ?") for _ in range(3)]
            answers.append(inst.query("SYST:ERR?"))
            return answers
        finally:
            inst.close()
    finally:
        rm.close()


def wait_for_close(path):
    deadline = time.monotonic() + T
    while time.monotonic() < deadline:
        if any(getattr(i, "kind", None) == "close" for i in Cassette.load(path).items):
            return
        time.sleep(0.02)
    raise AssertionError("recorder never saw the client close")


@pytest.fixture
def recorded(dmm, tmp_path):
    """Cassette of the script run against a noise-free DMM; yields (path, answers)."""
    dmm.instrument.inject("noise", sigma=0)
    path = tmp_path / "dmm.jsonl"
    with Recorder(("127.0.0.1", dmm.port), path) as rec:
        answers = run_script(rec.port)
        wait_for_close(path)
    return path, answers


def test_record_then_serve(recorded):
    path, answers = recorded
    assert len(answers) == 5
    with ReplayServer(path) as server:
        assert run_script(server.port) == answers
        assert server.problems == []


def test_check_against_same_sim_passes(dmm, recorded):
    path, _ = recorded
    report = check(path, ("127.0.0.1", dmm.port), timing="fast",
                   rules=[Rule("READ?", abs=0.01), Rule("*IDN?", compare="ignore")])
    assert report.ok, report.text()


def test_check_detects_injected_offset(dmm, recorded, tmp_path):
    path, _ = recorded
    rules = [Rule("READ?", abs=0.01), Rule("*IDN?", compare="ignore")]
    rules_toml = tmp_path / "rules.toml"
    rules_toml.write_text('[[rule]]\nmatch = "*IDN?"\ncompare = "ignore"\n\n'
                          '[[rule]]\nmatch = "READ?"\nabs = 0.01\n')
    target = f"127.0.0.1:{dmm.port}"
    argv = ["check", str(path), "--target", target, "--timing", "fast", "--rules", str(rules_toml)]
    assert main(argv) == 0

    dmm.instrument.inject("offset", value=1.0)
    report = check(path, ("127.0.0.1", dmm.port), timing="fast", rules=rules)
    assert not report.ok
    statuses = [(r.verdict.status, r.exchange.send.text) for r in report.results
                if r.exchange.send.text == "READ?"]
    assert statuses == [("FAIL", "READ?")] * 3
    assert main(argv) == 1
