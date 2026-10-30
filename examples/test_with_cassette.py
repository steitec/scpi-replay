"""Run driver code against a recorded cassette instead of a real DMM.

    pytest examples/test_with_cassette.py

Refresh the cassette from a real instrument (needs PyVISA-py):

    pytest examples/test_with_cassette.py --scpi-record --scpi-target 192.168.1.50
"""

from __future__ import annotations

from pathlib import Path

import pyvisa
import pytest

CASSETTE = Path(__file__).with_name("bench-dmm.scpi.jsonl")


def read_voltage(resource: str) -> float:
    """The code under test: configure the DMM and read one value."""
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(resource, read_termination="\n", write_termination="\n", timeout=5000)
    try:
        inst.write("CONF:VOLT:DC 10")
        return float(inst.query("READ?"))
    finally:
        inst.close()
        rm.close()


def test_reads_voltage(scpi_cassette):
    server = scpi_cassette(CASSETTE)  # lookup mode (default): the order of queries does not matter
    assert read_voltage(server.resource) == pytest.approx(5.0, abs=0.05)
