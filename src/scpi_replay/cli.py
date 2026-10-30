"""Command line interface: record, serve, check, show."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from . import __version__
from .cassette import Cassette, CassetteError
from .check import CheckError, check
from .compare import RulesError
from .proxy import Recorder
from .server import ReplayServer
from .show import transcript

DEFAULT_PORT = 5025


def parse_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """Parse ``HOST``, ``HOST:PORT`` or ``[IPV6]:PORT``; a bad address is a usage error (exit 2)."""
    try:
        return _parse_address(text, default_port)
    except ValueError as e:
        _build_parser().error(str(e))


def _parse_address(text: str, default_port: int) -> tuple[str, int]:
    host, port = text, default_port
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or (rest and not rest.startswith(":")):
            raise ValueError(f"invalid address {text!r}")
        port_text = rest[1:] if rest else ""
    elif text.count(":") == 1:
        host, _, port_text = text.partition(":")
    else:
        port_text = ""
    if port_text:
        if not port_text.isdigit() or int(port_text) > 65535:
            raise ValueError(f"invalid port in {text!r}")
        port = int(port_text)
    if not host:
        raise ValueError(f"missing host in {text!r}")
    return host, port


def _is_loopback(host: str) -> bool:
    return host == "localhost" or host == "::1" or host.startswith("127.")


def _address(default_port: int = DEFAULT_PORT):
    return lambda text: parse_address(text, default_port)


def _listen(text: str) -> tuple[str, int]:
    if text.isdigit():
        text = f"127.0.0.1:{text}"
    return _address()(text)


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scpi-replay",
                                description="Record SCPI raw-socket traffic and replay it.")
    p.add_argument("--version", action="version", version=f"scpi-replay {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("record", help="proxy a target and record the traffic")
    r.add_argument("--target", required=True, type=_address(), metavar="HOST[:PORT]")
    r.add_argument("--listen", type=_listen, metavar="[HOST:]PORT",
                   help="default 127.0.0.1:5025, or a free port if the target is local")
    r.add_argument("-o", "--output", required=True, metavar="FILE")
    r.add_argument("--note", default="")
    r.add_argument("--once", action="store_true", help="stop after the first client disconnects")

    s = sub.add_parser("serve", help="act as the recorded instrument")
    s.add_argument("file", metavar="FILE")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--mode", choices=["strict", "lookup"], default="lookup")
    s.add_argument("--conn", type=int)
    s.add_argument("--timing", choices=["fast", "recorded"], default="fast")
    s.add_argument("--max-delay", type=_positive, default=5.0, metavar="SECONDS")

    c = sub.add_parser("check", help="replay a cassette against a target and compare")
    c.add_argument("file", metavar="FILE")
    c.add_argument("--target", required=True, type=_address(), metavar="HOST[:PORT]")
    c.add_argument("--rules", metavar="RULES.toml")
    c.add_argument("--junit", metavar="OUT.xml")
    c.add_argument("--json", metavar="OUT.json")
    c.add_argument("--timing", choices=["recorded", "fast"], default="recorded")
    c.add_argument("--max-gap", type=_positive, default=10.0, metavar="SECONDS")
    c.add_argument("--timeout", type=_positive, default=5.0, metavar="SECONDS")
    c.add_argument("--conn", type=int)

    w = sub.add_parser("show", help="print a transcript")
    w.add_argument("file", metavar="FILE")
    w.add_argument("--conn", type=int)
    w.add_argument("--times", action="store_true")
    return p


def _say(text: str, file=None) -> None:
    print(text, file=file or sys.stdout, flush=True)


def _sigterm(signum, frame) -> None:
    raise KeyboardInterrupt


def _install_sigterm() -> None:
    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, OSError):  # not the main thread / unsupported platform
        pass


def _wait_forever() -> None:
    import threading
    stop = threading.Event()
    while not stop.wait(0.2):  # short waits keep Ctrl+C responsive on every platform
        pass


def _record(args) -> int:
    host, port = args.target
    if args.listen is not None:
        listen = args.listen
    else:
        listen = ("127.0.0.1", 0 if _is_loopback(host) else DEFAULT_PORT)
    rec = Recorder(args.target, args.output, host=listen[0], port=listen[1],
                   note=args.note, once=args.once)
    try:
        bound = rec.start()
    except OSError as e:
        print(f"scpi-replay: error: cannot listen on {listen[0]}:{listen[1]}: {e}", file=sys.stderr)
        return 2
    _install_sigterm()
    try:
        _say(f"recording {host}:{port} via {listen[0]}:{bound} -> {args.output}")
        try:
            if args.once:
                while not rec.finished.wait(0.2):
                    pass
            else:
                _wait_forever()
        except KeyboardInterrupt:
            pass
    finally:
        rec.stop()
    if not args.once:
        _say(f"recorded {rec.connections} connection(s) -> {args.output}")
    return 0


def _serve(args) -> int:
    try:
        srv = ReplayServer(args.file, host=args.host, port=args.port, mode=args.mode,
                           conn=args.conn, timing=args.timing, max_delay=args.max_delay)
        bound = srv.start()
    except (CassetteError, OSError) as e:
        print(f"scpi-replay: error: {e}", file=sys.stderr)
        return 2
    _install_sigterm()
    try:
        _say(f"serving {args.file} on {args.host}:{bound} ({args.mode})")
        try:
            _wait_forever()
        except KeyboardInterrupt:
            pass
    finally:
        srv.stop()
    problems = srv.problems
    summary = f"served {srv.served} queries, " \
              f"{sum(p.startswith('unknown query') for p in problems)} unknown, " \
              f"{sum(p.startswith('expected') for p in problems)} mismatches"
    if args.mode == "strict":
        summary += f", {len(srv.unreached())} recorded exchanges never reached"
    _say(summary, sys.stderr)
    for line in problems:
        _say(line, sys.stderr)
    return 1 if problems else 0


def _check(args) -> int:
    try:
        report = check(args.file, args.target, rules=args.rules, timing=args.timing,
                       max_gap=args.max_gap, timeout=args.timeout, conn=args.conn)
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        _say(report.text(color=color).rstrip("\n"))
        if args.junit:
            Path(args.junit).write_text(report.to_junit(), encoding="utf-8")
        if args.json:
            Path(args.json).write_text(report.to_json(), encoding="utf-8")
    except (CheckError, CassetteError, RulesError, OSError) as e:
        print(f"scpi-replay: error: {e}", file=sys.stderr)
        return 2
    return 0 if report.ok else 1


def _show(args) -> int:
    try:
        cassette = Cassette.load(args.file)
    except CassetteError as e:
        print(f"scpi-replay: error: {e}", file=sys.stderr)
        return 2
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    sys.stdout.write(transcript(cassette, args.conn, args.times, color))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {"record": _record, "serve": _serve, "check": _check, "show": _show}[args.command](args)
