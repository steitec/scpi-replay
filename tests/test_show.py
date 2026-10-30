import json

from scpi_replay.cassette import Cassette
from scpi_replay.show import transcript


def make(tmp_path):
    rows = [{"format": "scpi-replay/1", "recorded": "x", "target": "h:1", "tool": "t", "note": ""},
            {"t": 0.0, "conn": 1, "event": "open"},
            {"t": 0.001, "conn": 1, "dir": "send", "data": "*IDN?"},
            {"t": 0.012, "conn": 1, "dir": "recv", "data": "ACME,1"},
            {"t": 0.02, "conn": 1, "dir": "recv", "b64": "IzEzYWJj", "binary": True},
            {"t": 0.03, "conn": 1, "warning": "careful"},
            {"t": 0.04, "conn": 1, "event": "close", "by": "target"}]
    p = tmp_path / "c.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_transcript(tmp_path):
    out = transcript(Cassette.load(make(tmp_path)))
    lines = out.splitlines()
    assert lines[0] == "-- conn 1 open"
    assert "> *IDN?" in lines and "< ACME,1" in lines
    assert any(l.startswith("< <binary ") and l.endswith(" bytes>") for l in lines)
    assert "!! careful" in lines
    assert lines[-1] == "-- conn 1 closed by target"
    assert "\x1b" not in out


def test_times_and_color_and_conn(tmp_path):
    c = Cassette.load(make(tmp_path))
    assert "  0.012 < ACME,1" in transcript(c, times=True).splitlines()
    assert "\x1b[" in transcript(c, color=True)
    assert transcript(c, conn=2).strip() == "!! careful"
