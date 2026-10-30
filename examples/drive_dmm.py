"""Drive a DMM through the recorder: the session behind bench-dmm.scpi.jsonl.

    scpi-replay record --target 127.0.0.1:5025 --listen 0 --once -o bench-dmm.scpi.jsonl
    python drive_dmm.py <port printed by the recorder>

Needs PyVISA and PyVISA-py (pip install pyvisa pyvisa-py).
"""

from __future__ import annotations

import sys

import pyvisa


def main(port: int, host: str = "127.0.0.1") -> None:
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(f"TCPIP::{host}::{port}::SOCKET",
                            read_termination="\n", write_termination="\n", timeout=5000)
    try:
        print(inst.query("*IDN?").strip())
        inst.write("CONF:VOLT:DC 10")
        for _ in range(3):
            print(inst.query("READ?").strip())
        print(inst.query("SYST:ERR?").strip())
    finally:
        inst.close()
        rm.close()


if __name__ == "__main__":
    main(int(sys.argv[1]))
