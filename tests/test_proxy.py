import socket
import time

import pytest
from helpers import EchoInstrument

from scpi_replay.cassette import Cassette
from scpi_replay.proxy import Recorder

T = 5


def connect(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=T)
    return s


def read_until(s, n=None, suffix=b"\n"):
    buf = b""
    while not (buf.endswith(suffix) if n is None else len(buf) >= n):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "c.jsonl"
    with EchoInstrument() as inst:
        with Recorder(("127.0.0.1", inst.port), path) as rec:
            yield inst, rec, path


def settle(path, closes=1):
    """Wait until the recorder has written `closes` close events (stop() would cancel it)."""
    deadline = time.monotonic() + T
    while time.monotonic() < deadline:
        if len(events(Cassette.load(path), "close")) >= closes:
            return
        time.sleep(0.01)
    raise AssertionError("no close event")


def events(cas, kind):
    return [i for i in cas.items if getattr(i, "kind", None) == kind]


def test_records_query_and_response(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"A?\n")
        assert read_until(s) == b"ans:A?\n"
    settle(path)
    rec.stop()
    cas = Cassette.load(path)
    ex = cas.exchanges()
    assert ex[0].send.text == "A?" and ex[0].response.text == "ans:A?"
    assert [e.by for e in events(cas, "close")] == ["client"]
    assert len(events(cas, "open")) == 1


def test_pipelined_writes(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"SET 1\nA?\n")
        assert read_until(s) == b"ans:A?\n"
    settle(path)
    rec.stop()
    ex = Cassette.load(path).exchanges()
    assert len(ex) == 2
    assert ex[1].response.text == "ans:A?"
    assert bytes(inst.received) == b"SET 1\nA?\n"


def test_binary_forwarded_unchanged(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"BIN?\n")
        assert read_until(s, n=9) == b"#14ab\ncd\n"
    settle(path)
    rec.stop()
    recv = [m for m in Cassette.load(path).items if getattr(m, "dir", None) == "recv"]
    assert len(recv) == 1 and recv[0].binary


def test_target_closes(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"BYE\n")
        assert s.recv(10) == b""
    settle(path)
    rec.stop()
    assert [e.by for e in events(Cassette.load(path), "close")] == ["target"]


def test_target_refused(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    path = tmp_path / "c.jsonl"
    with Recorder(("127.0.0.1", dead), path) as rec:
        with connect(rec.port) as s:
            assert s.recv(10) == b""
    warnings = events(Cassette.load(path), "warning")
    assert warnings and "refused" in warnings[0].text


def test_two_clients_get_conn_numbers(setup):
    inst, rec, path = setup
    for _ in range(2):
        with connect(rec.port) as s:
            s.sendall(b"A?\n")
            read_until(s)
    settle(path, 2)
    rec.stop()
    assert Cassette.load(path).connections() == [1, 2]


def test_once_sets_finished(tmp_path):
    path = tmp_path / "c.jsonl"
    with EchoInstrument() as inst, Recorder(("127.0.0.1", inst.port), path, once=True) as rec:
        assert not rec.finished.is_set()
        with connect(rec.port) as s:
            s.sendall(b"A?\n")
            read_until(s)
        assert rec.finished.wait(T)
        assert len(events(Cassette.load(path), "close")) == 1


def test_finished_stays_unset_without_once(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"A?\n")
        read_until(s)
    settle(path)
    assert not rec.finished.is_set()


def test_leftover_at_close_is_recorded(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"HALF\n")
        assert read_until(s, n=1) == b"X"
        assert s.recv(10) == b""
    settle(path)
    rec.stop()
    cas = Cassette.load(path)
    recv = [m for m in cas.items if getattr(m, "dir", None) == "recv"]
    assert [m.raw for m in recv] == [b"X"]
    assert any("incomplete message" in w.text for w in events(cas, "warning"))


def test_cassette_errors_do_not_break_forwarding(setup, monkeypatch):
    inst, rec, path = setup

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rec._cassette, "message", boom)
    monkeypatch.setattr(rec._cassette, "event", boom)
    with connect(rec.port) as s:
        s.sendall(b"A?\n")
        assert read_until(s) == b"ans:A?\n"
        s.sendall(b"B?\n")
        assert read_until(s) == b"ans:B?\n"
    rec.stop()
    assert bytes(inst.received) == b"A?\nB?\n"


def test_stop_records_unfinished_data(setup):
    inst, rec, path = setup
    with connect(rec.port) as s:
        s.sendall(b"A?\n")
        assert read_until(s) == b"ans:A?\n"
        s.sendall(b"PART")
        deadline = time.monotonic() + T
        while bytes(inst.received) != b"A?\n" and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)  # let the proxy forward PART
        rec.stop()
    cas = Cassette.load(path)
    sends = [m.raw for m in cas.items if getattr(m, "dir", None) == "send"]
    assert sends == [b"A?", b"PART"]
    assert any("incomplete message" in w.text for w in events(cas, "warning"))
    assert [e.by for e in events(cas, "close")] == ["recorder"]


def test_failed_start_leaves_file_alone(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text("PRECIOUS")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        rec = Recorder(("127.0.0.1", 1), path, port=busy.getsockname()[1])
        with pytest.raises(OSError):
            rec.start()
    assert path.read_text() == "PRECIOUS"
    rec.stop()
