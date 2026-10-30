"""Cassette model: JSONL writer, loader and exchange grouping."""

from __future__ import annotations

import base64
import binascii
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import __version__
from .framing import Frame
from .scpi import is_query

FORMAT = "scpi-replay/1"


class CassetteError(ValueError):
    """The cassette file is missing, malformed or of an unknown format."""


@dataclass(frozen=True)
class Message:
    t: float
    conn: int
    dir: Literal["send", "recv"]
    raw: bytes
    binary: bool
    term: bytes

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Event:
    t: float
    conn: int
    kind: Literal["open", "close", "warning"]
    by: str | None = None
    text: str = ""


@dataclass
class Exchange:
    index: int  # 1-based position in the returned list
    conn: int
    send: Message
    response: Message | None
    query: bool
    gap: float = 0.0  # seconds since the previous item on the same conn
    latency: float | None = None  # response.t - send.t


class CassetteWriter:
    """Append-only JSONL writer; thread-safe, flushes after every line."""

    def __init__(self, path: str | Path, *, target: str, note: str = "") -> None:
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._f = open(path, "wb")
        self._line({
            "format": FORMAT,
            "recorded": datetime.now().astimezone().isoformat(timespec="seconds"),
            "target": target,
            "tool": f"scpi-replay {__version__}",
            "note": note,
        })

    def _line(self, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self._f.write(data)
            self._f.flush()

    def _t(self) -> float:
        return round(time.monotonic() - self._start, 3)

    def message(self, conn: int, dir: str, frame: Frame) -> None:
        obj: dict[str, Any] = {"t": self._t(), "conn": conn, "dir": dir}
        text = None
        if not frame.binary:
            try:
                text = frame.data.decode("utf-8")
            except UnicodeDecodeError:
                pass
        if text is not None:
            obj["data"] = text
        else:
            obj["b64"] = base64.b64encode(frame.data).decode("ascii")
            if frame.binary:
                obj["binary"] = True
        if frame.term != b"\n":
            obj["term"] = frame.term.decode("ascii")
        self._line(obj)

    def event(self, conn: int, kind: str, *, by: str | None = None, text: str = "") -> None:
        obj: dict[str, Any] = {"t": self._t(), "conn": conn}
        if kind == "warning":
            obj["warning"] = text
        else:
            obj["event"] = kind
            if by:
                obj["by"] = by
        self._line(obj)

    def close(self) -> None:
        with self._lock:
            if not self._f.closed:
                self._f.close()

    def __enter__(self) -> CassetteWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _parse_item(row: dict[str, Any]) -> Message | Event:
    t, conn = row["t"], row["conn"]
    if not isinstance(t, (int, float)) or isinstance(t, bool):
        raise ValueError("'t' must be a number")
    if not isinstance(conn, int) or isinstance(conn, bool):
        raise ValueError("'conn' must be an integer")
    if "dir" in row:
        d = row["dir"]
        if d not in ("send", "recv"):
            raise ValueError(f"invalid dir {d!r}")
        if "data" in row:
            if not isinstance(row["data"], str):
                raise ValueError("'data' must be a string")
            raw = row["data"].encode("utf-8")
        elif "b64" in row:
            try:
                raw = base64.b64decode(row["b64"], validate=True)
            except (binascii.Error, TypeError, ValueError):
                raise ValueError("invalid 'b64'") from None
        else:
            raise ValueError("message needs 'data' or 'b64'")
        term = row.get("term", "\n")
        if term not in ("\n", "\r\n", ""):
            raise ValueError(f"invalid term {term!r}")
        binary = row.get("binary") is True and "b64" in row
        return Message(float(t), conn, d, raw, binary, term.encode("ascii"))
    if "event" in row:
        if row["event"] not in ("open", "close"):
            raise ValueError(f"invalid event {row['event']!r}")
        by = row.get("by")
        return Event(float(t), conn, row["event"], by if isinstance(by, str) else None)
    if "warning" in row:
        return Event(float(t), conn, "warning", text=str(row["warning"]))
    raise ValueError("line has no 'dir', 'event' or 'warning'")


@dataclass
class Cassette:
    header: dict[str, Any]
    items: list[Message | Event]
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise CassetteError(f"cannot read {path}: {e}") from None
        numbered = [(n, s) for n, s in enumerate(text.splitlines(), 1) if s.strip()]
        if not numbered:
            raise CassetteError("line 1: empty cassette (no header)")
        header: dict[str, Any] | None = None
        items: list[Message | Event] = []
        warnings: list[str] = []
        for pos, (n, line) in enumerate(numbered):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                if pos == len(numbered) - 1 and header is not None:
                    warnings.append(f"line {n}: truncated last line ignored ({e.msg})")
                    break
                raise CassetteError(f"line {n}: invalid JSON ({e.msg})") from None
            if not isinstance(row, dict):
                raise CassetteError(f"line {n}: expected a JSON object")
            if header is None:
                if row.get("format") != FORMAT:
                    raise CassetteError(
                        f"line {n}: unsupported format {row.get('format')!r}, expected {FORMAT!r}"
                    )
                header = row
                continue
            try:
                items.append(_parse_item(row))
            except KeyError as e:
                raise CassetteError(f"line {n}: missing key {e.args[0]!r}") from None
            except ValueError as e:
                raise CassetteError(f"line {n}: {e}") from None
        assert header is not None
        return cls(header, items, warnings)

    def connections(self) -> list[int]:
        return sorted({i.conn for i in self.items if not (isinstance(i, Event) and i.kind == "warning")})

    def _walk(self, conn: int) -> tuple[list[Exchange], list[Message]]:
        exchanges: list[Exchange] = []
        unsolicited: list[Message] = []
        pending: Exchange | None = None
        prev_t: float | None = None
        for it in self.items:
            if it.conn != conn:
                continue
            if isinstance(it, Event):
                if it.kind == "warning":
                    continue
                if it.kind == "close":
                    pending = None
                if it.kind == "open":
                    prev_t = it.t
                continue
            if it.dir == "send":
                q = is_query(it.text)
                ex = Exchange(0, conn, it, None, q, 0.0 if prev_t is None else it.t - prev_t)
                exchanges.append(ex)
                pending = ex if q else None
            elif pending is not None:
                pending.response = it
                pending.latency = it.t - pending.send.t
                pending = None
            else:
                unsolicited.append(it)
            prev_t = it.t
        return exchanges, unsolicited

    def exchanges(self, conn: int | None = None) -> list[Exchange]:
        conns = self.connections() if conn is None else [conn]
        out: list[Exchange] = []
        for c in conns:
            out.extend(self._walk(c)[0])
        for i, ex in enumerate(out, 1):
            ex.index = i
        return out

    def unsolicited(self) -> list[Message]:
        out: list[Message] = []
        for c in self.connections():
            out.extend(self._walk(c)[1])
        return out
