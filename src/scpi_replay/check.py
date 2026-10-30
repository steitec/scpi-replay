"""``check``: replay a cassette's commands against a live target and compare."""

from __future__ import annotations

import json
import socket
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .cassette import Cassette, Exchange
from .compare import Rule, Verdict, compare, find_rule, load_rules
from .framing import Frame, Framer


class CheckError(Exception):
    """The target cannot be reached."""


class CheckInterrupted(KeyboardInterrupt):
    """Ctrl+C during :func:`check`; ``report`` holds the results so far (aborted: "interrupted")."""

    def __init__(self, report: Report) -> None:
        super().__init__("interrupted")
        self.report = report


@dataclass
class Result:
    exchange: Exchange
    actual: bytes | None
    verdict: Verdict

    @property
    def label(self) -> str:
        return f"{self.exchange.index:03d} {self.exchange.send.text}"


def _show(raw: bytes, binary: bool) -> str:
    return f"<binary {len(raw)} bytes>" if binary else raw.decode("utf-8", "replace")


@dataclass
class Report:
    cassette: str
    target: str
    results: list[Result] = field(default_factory=list)
    aborted: str | None = None

    @property
    def ok(self) -> bool:
        return self.aborted is None and not any(r.verdict.status == "FAIL" for r in self.results)

    @property
    def counts(self) -> dict[str, int]:
        c = {"PASS": 0, "FAIL": 0, "IGNORED": 0}
        for r in self.results:
            c[r.verdict.status] += 1
        return c

    def text(self, color: bool = False) -> str:
        paint = {"FAIL": "31", "PASS": "32", "IGNORED": "33"}
        ordered = ([r for r in self.results if r.verdict.status == "FAIL"]
                   + [r for r in self.results if r.verdict.status != "FAIL"])
        lines = []
        for r in ordered:
            st = r.verdict.status
            tag = f"\x1b[{paint[st]}m{st}\x1b[0m" if color else st
            line = f"{tag}{' ' * (2 if len(st) < 6 else 1)}{r.label}"
            if r.verdict.reason:
                line += f"  {r.verdict.reason}"
            lines.append(line)
        if self.aborted:
            lines.append(f"aborted: {self.aborted}")
        c = self.counts
        lines.append(f"{len(self.results)} queries: {c['PASS']} passed, {c['FAIL']} failed, {c['IGNORED']} ignored")
        return "\n".join(lines)

    def _recorded(self, r: Result) -> str:
        m = r.exchange.response
        return "(none)" if m is None else _show(m.raw, m.binary)

    def _actual(self, r: Result) -> str:
        if r.actual is None:
            return "(none)"
        return _show(r.actual, r.actual.startswith(b"#"))

    def to_junit(self) -> str:
        c = self.counts
        suite = ET.Element("testsuite", name=self.cassette, tests=str(len(self.results)),
                           failures=str(c["FAIL"]), skipped=str(c["IGNORED"]))
        for r in self.results:
            case = ET.SubElement(suite, "testcase", name=r.label, classname="scpi-replay")
            st = r.verdict.status
            if st == "FAIL":
                f = ET.SubElement(case, "failure", message=r.verdict.reason)
                f.text = f"recorded: {self._recorded(r)}\nactual: {self._actual(r)}"
            elif st == "IGNORED":
                ET.SubElement(case, "skipped")
        if self.aborted:
            ET.SubElement(suite, "system-err").text = f"aborted: {self.aborted}"
        ET.indent(suite)
        return ET.tostring(suite, encoding="unicode", xml_declaration=False)

    def to_json(self) -> str:
        results = []
        for r in self.results:
            results.append({
                "index": r.exchange.index,
                "command": r.exchange.send.text,
                "status": r.verdict.status,
                "reason": r.verdict.reason,
                "expected": None if r.exchange.response is None else self._recorded(r),
                "actual": None if r.actual is None else self._actual(r),
            })
        return json.dumps({"cassette": self.cassette, "target": self.target, "ok": self.ok,
                           "aborted": self.aborted, "counts": self.counts, "results": results},
                          indent=2, ensure_ascii=False)


class _Lost(Exception):
    pass


class _Link:
    """Blocking connection with a framer.

    Before every send, answers that are already there (late or extra ones) are
    discarded, so they are never compared with the next query's answer.
    """

    def __init__(self, sock: socket.socket, timeout: float) -> None:
        self.sock = sock
        self.timeout = timeout
        self.framer = Framer()
        self.pending: deque[Frame] = deque()

    def discard_stale(self) -> None:
        """Drop pending frames and any bytes already waiting in the socket."""
        self.pending.clear()
        self.framer = Framer()
        self.sock.settimeout(0.0)
        try:
            while True:
                try:
                    chunk = self.sock.recv(65536)
                except (BlockingIOError, InterruptedError):
                    return
                except OSError as e:
                    raise _Lost from e
                if not chunk:
                    raise _Lost
        finally:
            self.sock.settimeout(self.timeout)

    def send(self, data: bytes) -> None:
        self.discard_stale()
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise _Lost from e

    def read(self, timeout: float) -> bytes | None:
        """Next response, or None on timeout. Raises _Lost on EOF/reset."""
        deadline = time.monotonic() + timeout
        while not self.pending:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            except OSError as e:
                raise _Lost from e
            if not chunk:
                tail = self.framer.flush()
                if tail is None:
                    raise _Lost
                self.pending.append(tail)
                break
            self.pending.extend(self.framer.feed(chunk))
        return self.pending.popleft().data


def _wait_for(ex: Exchange, timeout: float) -> float:
    """How long to wait for the answer: at least ``timeout``, longer for slow recorded answers."""
    if ex.response is None:
        return timeout
    return max(timeout, 2 * ex.latency + 0.5)


def check(cassette: Cassette | str | Path, target: tuple[str, int], *,
          rules: list[Rule] | str | Path | None = None,
          timing: Literal["recorded", "fast"] = "recorded", max_gap: float = 10.0,
          timeout: float = 5.0, conn: int | None = None) -> Report:
    """Send every recorded command to ``target`` and compare the query responses.

    Raises :class:`CheckError` if the target cannot be reached, ValueError
    for an unknown ``conn`` and :class:`CheckInterrupted` on Ctrl+C.
    """
    if isinstance(cassette, Cassette):
        name = "<memory>"
    else:
        name = str(cassette)
        cassette = Cassette.load(cassette)
    cassette.require_conn(conn, name)
    if rules is None:
        rule_list: list[Rule] = []
    elif isinstance(rules, list):
        rule_list = rules
    else:
        rule_list = load_rules(rules)
    host, port = target
    exchanges = cassette.exchanges(conn)
    try:
        sock = socket.create_connection(target, timeout=timeout)
    except OSError as e:
        raise CheckError(f"cannot connect to {host}:{port}: {e}") from None
    report = Report(name, f"{host}:{port}")
    with sock:
        link = _Link(sock, timeout)
        try:
            for pos, ex in enumerate(exchanges):
                if timing == "recorded" and ex.gap > 0:
                    time.sleep(min(ex.gap, max_gap))
                rule = find_rule(rule_list, ex.send.text) if ex.query else None
                sent = False
                try:
                    link.send(ex.send.raw + (ex.send.term or b"\n"))
                    sent = True
                    if not ex.query:
                        continue
                    actual = link.read(_wait_for(ex, timeout))
                except _Lost:
                    report.aborted = f"connection lost at {ex.index:03d} {ex.send.text}"
                    for e in exchanges[pos:]:
                        if not e.query:
                            continue
                        reason = ("connection lost before response" if e is ex and sent
                                  else "not sent: connection lost")
                        report.results.append(Result(e, None, Verdict("FAIL", reason)))
                    break
                assert rule is not None
                report.results.append(Result(ex, actual, compare(ex.response, actual, rule)))
        except KeyboardInterrupt:
            report.aborted = "interrupted"
            raise CheckInterrupted(report) from None
    return report
