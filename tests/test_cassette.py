import json

import pytest

from scpi_replay.cassette import Cassette, CassetteError, CassetteWriter
from scpi_replay.framing import Frame


def F(s, term=b"\n", binary=False):
    return Frame(s if isinstance(s, bytes) else s.encode(), binary, term)


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def write(path, *msgs, target="host:5025"):
    with CassetteWriter(path, target=target) as w:
        for conn, d, frame in msgs:
            w.message(conn, d, frame)


def test_roundtrip(tmp_path):
    p = tmp_path / "a.scpi.jsonl"
    with CassetteWriter(p, target="10.0.0.1:5025", note="hi") as w:
        w.event(1, "open")
        w.message(1, "send", F("*IDN?"))
        w.message(1, "recv", F("ACME,X,1,1.0"))
        w.message(1, "send", F("OUTP ON", b"\r\n"))
        w.message(1, "recv", F(b"#13abc", b"", True))
        w.event(1, "close", by="client")
    c = Cassette.load(p)
    assert c.header["format"] == "scpi-replay/1"
    assert c.header["target"] == "10.0.0.1:5025"
    assert c.header["note"] == "hi"
    assert c.header["tool"] == "scpi-replay 0.1.0"
    msgs = [i for i in c.items if hasattr(i, "raw")]
    assert [(m.raw, m.binary, m.term) for m in msgs] == [
        (b"*IDN?", False, b"\n"),
        (b"ACME,X,1,1.0", False, b"\n"),
        (b"OUTP ON", False, b"\r\n"),
        (b"#13abc", True, b""),
    ]
    assert msgs[0].text == "*IDN?"
    ev = [i for i in c.items if not hasattr(i, "raw")]
    assert [(e.kind, e.by) for e in ev] == [("open", None), ("close", "client")]
    assert c.connections() == [1]


def test_json_shape(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "send", F("A")), (1, "send", F("B", b"\r\n")),
          (1, "recv", F(b"#13abc", b"", True)), (1, "send", F("C", b"")))
    h, a, b, bin_, c = lines(p)
    assert a["data"] == "A" and "term" not in a and "b64" not in a
    assert b["term"] == "\r\n"
    assert bin_["b64"] and "data" not in bin_ and bin_["binary"] is True
    assert bin_["term"] == ""
    assert c["term"] == ""


def test_non_utf8_text_stored_as_b64(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "recv", F(b"\xff\xfe")))
    row = lines(p)[1]
    assert "b64" in row and "data" not in row and "binary" not in row
    m = Cassette.load(p).items[0]
    assert m.raw == b"\xff\xfe" and m.binary is False


def test_exchanges_pairing(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "send", F("CONF:VOLT 10")), (1, "send", F("READ?")),
          (1, "recv", F("+1.2E+01")))
    ex = Cassette.load(p).exchanges()
    assert [e.index for e in ex] == [1, 2]
    assert ex[0].query is False and ex[0].response is None
    assert ex[0].gap == 0 and ex[0].latency is None
    assert ex[1].query is True and ex[1].response.text == "+1.2E+01"
    assert ex[1].latency == pytest.approx(ex[1].response.t - ex[1].send.t)
    assert ex[1].latency >= 0


def test_latency_and_gap_from_t(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(
        '{"format":"scpi-replay/1"}\n'
        '{"t":0.0,"conn":1,"event":"open"}\n'
        '{"t":0.5,"conn":1,"dir":"send","data":"X?"}\n'
        '{"t":0.75,"conn":1,"dir":"recv","data":"1"}\n'
        '{"t":2.0,"conn":1,"dir":"send","data":"Y?"}\n'
    )
    ex = Cassette.load(p).exchanges()
    assert ex[0].gap == pytest.approx(0.5)
    assert ex[0].latency == pytest.approx(0.25)
    assert ex[1].gap == pytest.approx(1.25)


def test_query_without_response(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "send", F("BAD?")), (1, "send", F("*IDN?")), (1, "recv", F("X")))
    ex = Cassette.load(p).exchanges()
    assert ex[0].response is None and ex[0].query
    assert ex[1].response.text == "X"


def test_unsolicited(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "recv", F("hello")), (1, "send", F("SET 1")), (1, "recv", F("late")))
    c = Cassette.load(p)
    assert [m.text for m in c.unsolicited()] == ["hello", "late"]
    assert len(c.exchanges()) == 1 and c.exchanges()[0].response is None


def test_conn_filter(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (2, "send", F("B?")), (1, "send", F("A?")), (2, "recv", F("b")),
          (1, "recv", F("a")))
    c = Cassette.load(p)
    assert c.connections() == [1, 2]
    assert [e.send.text for e in c.exchanges(2)] == ["B?"]
    ex = c.exchanges()
    assert [(e.conn, e.index) for e in ex] == [(1, 1), (2, 2)]
    assert ex[0].response.text == "a"
    assert c.exchanges(2)[0].index == 1


def test_truncated_last_line_warns(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, (1, "send", F("A")))
    with open(p, "a") as f:
        f.write('{"t": 1.0, "conn"')
    c = Cassette.load(p)
    assert len(c.warnings) == 1 and "line 3" in c.warnings[0]
    assert len(c.items) == 1


def test_warning_line_roundtrip(tmp_path):
    p = tmp_path / "a.jsonl"
    with CassetteWriter(p, target="t") as w:
        w.event(1, "warning", text="odd")
    e = Cassette.load(p).items[0]
    assert (e.kind, e.text) == ("warning", "odd")


def test_bad_header_raises(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text("")
    with pytest.raises(CassetteError, match="line 1"):
        Cassette.load(p)
    p.write_text('{"format": "other/9"}\n')
    with pytest.raises(CassetteError, match="line 1"):
        Cassette.load(p)
    p.write_text('{"format": "scpi-replay/1"}\nnot json\n{"t":0,"conn":1,"event":"open"}\n')
    with pytest.raises(CassetteError, match="line 2"):
        Cassette.load(p)
    p.write_text('{"format": "scpi-replay/1"}\n{"t":0,"conn":1,"dir":"send"}\n'
                 '{"t":0,"conn":1,"event":"open"}\n')
    with pytest.raises(CassetteError, match="line 2"):
        Cassette.load(p)
