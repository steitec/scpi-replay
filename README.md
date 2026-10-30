<a href="https://steitec.net"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-dark.svg">
  <img align="right" height="32" alt="steitec" src="https://raw.githubusercontent.com/steitec/.github/main/brand/steitec-logo-on-light.svg">
</picture></a>

# scpi-replay

[![CI](https://github.com/steitec/scpi-replay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/steitec/scpi-replay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/scpi-replay)](https://pypi.org/project/scpi-replay/)
[![Python](https://img.shields.io/pypi/pyversions/scpi-replay)](https://pypi.org/project/scpi-replay/)
[![License](https://img.shields.io/pypi/l/scpi-replay)](https://github.com/steitec/scpi-replay/blob/main/LICENSE)

Record what a test program and a real instrument say to each other, keep it as
a file (a "cassette"), and use it later without the hardware. Test software for
benches needs instruments to run at all, and a simulator never behaves exactly
like the real device. scpi-replay records the real conversation through a
transparent TCP proxy on the instrument's raw SCPI socket, then replays it as a
stand-in instrument, as a regression check against a real instrument or a
simulator, or as a readable transcript.

<!-- docs/demo.gif goes here once recorded with vhs (docs/demo.tape) -->

```console
$ scpi-replay record --target 192.168.1.50 -o dmm.scpi.jsonl     # run the test software through the proxy
$ scpi-replay serve dmm.scpi.jsonl                               # later: the cassette answers instead
$ scpi-replay check dmm.scpi.jsonl --target 192.168.1.50         # replay the commands, compare the answers
$ scpi-replay show dmm.scpi.jsonl                                # print the transcript
```

Pure Python standard library, no runtime dependencies, Python 3.10 or newer,
Linux, Windows and macOS.

## Install

```bash
pip install scpi-replay
```

TOML rule files need `tomllib`, which comes with Python 3.11. On Python 3.10
install the extra: `pip install "scpi-replay[toml]"`.

## Quick start

All outputs below were produced with the files in [`examples/`](examples/): a
session with a simulated multimeter ([scpi-sim](https://github.com/steitec/scpi-sim)),
recorded through the proxy.

### `record`: put the proxy between the test software and the instrument

```console
$ scpi-replay record --target 192.168.1.50:5025 --listen 5030 -o dmm.scpi.jsonl
recording 192.168.1.50:5025 via 127.0.0.1:5030 -> dmm.scpi.jsonl
```

Point the test software at `127.0.0.1:5030` (for PyVISA:
`TCPIP::127.0.0.1::5030::SOCKET`) and run it. Bytes are forwarded unchanged in
both directions; the cassette is written message by message and flushed after
each one, so a crash loses nothing. The recorder runs until Ctrl+C, or with
`--once` until the first client disconnects (handy in scripts). Several clients
are fine, each gets its own connection number and its own connection to the
target. `--listen` defaults to `127.0.0.1:5025`, or to a free port (printed) if
the target is local. The target port defaults to 5025; connecting to it times
out after 10 s. If the target refuses or does not answer, the client is
disconnected, and a warning is written to the cassette and printed on stderr
(`scpi-replay: warning: ...`). With `--once` the exit code is 1 if no
connection to the target was ever opened, else 0.

[`examples/drive_dmm.py`](examples/drive_dmm.py) is the PyVISA session that
produced [`examples/bench-dmm.scpi.jsonl`](examples/bench-dmm.scpi.jsonl).

### `show`: read a cassette

```console
$ scpi-replay show examples/bench-dmm.scpi.jsonl
-- conn 1 open
> *IDN?
< scpi-sim,DMM,SIM00001,0.5.0
> CONF:VOLT:DC 10
> READ?
< +4.99958428E+00
> READ?
< +4.99975954E+00
> READ?
< +4.99982347E+00
> SYST:ERR?
< 0,"No error"
-- conn 1 closed by client
```

`>` is sent to the instrument, `<` comes back. Binary blocks are shown as
`<binary N bytes>`; `--times` adds timestamps, `--conn N` selects one
connection. Colour is used on a terminal only, and `NO_COLOR` is respected.

### `check`: replay the commands against an instrument and compare

```console
$ scpi-replay check examples/bench-dmm.scpi.jsonl --target 127.0.0.1:5025 --rules examples/rules.toml --timing fast
IGNORED 001 *IDN?
PASS  003 READ?
PASS  004 READ?
PASS  005 READ?
PASS  006 SYST:ERR?
5 queries: 4 passed, 0 failed, 1 ignored
```

The same check against a DMM that reads 0.5 V too high (`scpi-sim serve dmm
--volts 5.5`):

```console
$ scpi-replay check examples/bench-dmm.scpi.jsonl --target 127.0.0.1:5025 --rules examples/rules.toml --timing fast
FAIL  003 READ?  expected +4.99958428E+00, got +5.50023669E+00
FAIL  004 READ?  expected +4.99975954E+00, got +5.50020550E+00
FAIL  005 READ?  expected +4.99982347E+00, got +5.50034813E+00
IGNORED 001 *IDN?
PASS  006 SYST:ERR?
5 queries: 1 passed, 3 failed, 1 ignored
$ echo $?
1
```

`check` opens one connection to the target and sends every recorded command in
order (binary payloads byte for byte). After a query it reads one answer and
compares it with the recording. Failures are listed first.

- `--timing recorded` (default) waits the recorded gap before each command, capped
  by `--max-gap` (default 10 s), because real instruments need settle time after
  `OUTP ON` or `INIT`. `--timing fast` sends right away, good for simulators.
- `--timeout` (default 5 s) is the shortest wait for one answer. If the
  recorded answer took longer, `check` waits up to twice the recorded time plus
  0.5 s, so a slow `*OPC?` is not cut off. No answer counts as FAIL. Answers
  that arrive late, or extra lines after an answer, are discarded before the
  next command is sent, never compared with the next query.
  If the connection is lost, the run stops and every remaining query counts as FAIL.
- `--junit OUT.xml` writes JUnit XML (one test case per query, named like
  `003 READ?`), `--json OUT.json` the same data for scripts. test-protocol can
  read the JUnit file as it is.
- `--conn N` checks one recorded connection only.
- Ctrl+C stops the run and prints the queries checked so far with
  `aborted: interrupted` (the JUnit and JSON files are written too); the exit
  code is 1. Before the connection is made, Ctrl+C exits with 130.
- `check` sends every recorded command to the target, including `OUTP ON` or
  `*RST`. Point it only at an instrument where that is safe, and note that a run
  that is stopped or loses the connection can leave an output switched on.
- Exit codes: 0 all passed, 1 at least one FAIL (or interrupted), 2 usage error
  (including a `--conn` that is not in the cassette), cannot connect, or bad cassette.

### `serve`: let the cassette be the instrument

```console
$ scpi-replay serve examples/bench-dmm.scpi.jsonl --port 5025
serving examples/bench-dmm.scpi.jsonl on 127.0.0.1:5025 (lookup)
^C
served 5 queries, 0 unknown, 0 mismatches
```

The server listens on a raw socket (default `127.0.0.1:5025`) and answers from
the cassette.

- `--mode lookup` (default): each query is matched by its normalised command.
  Repeated queries get the recorded answers in recorded order, then the last
  one again. A query that is not in the cassette gets **no answer**, so the
  client times out like on a real instrument that does not know the command;
  it is logged on stderr as it happens (`scpi-replay: unknown query: X?`). Commands without a `?` are
  accepted silently.
- `--mode strict`: the client must send exactly the recorded commands in the
  recorded order. On the first mismatch the server logs `scpi-replay: expected X, got Y` and
  closes the connection, so the test fails fast instead of timing out.
- `--conn N` serves one recorded connection (default: all of them, one after
  the other). Every new client starts from the beginning.
- `--timing recorded` waits the recorded query-to-answer time before replying
  (capped by `--max-delay`, default 5 s). The default `fast` replies at once.
- On Ctrl+C the server prints a summary: queries served, unknown queries,
  mismatches, and in strict mode the recorded exchanges never reached. The exit
  code is 1 if there were unknown queries or mismatches, else 0. On Windows,
  Ctrl+C prints the summary, but `taskkill` ends the process without it.

## Cassette format

A cassette is a JSON Lines file (`*.scpi.jsonl`, UTF-8), one object per line,
easy to diff in git and to fix by hand. The first line is the header:

```json
{"format": "scpi-replay/1", "recorded": "2026-10-30T19:28:53+01:00", "target": "127.0.0.1:44777", "tool": "scpi-replay 0.1.0", "note": ""}
```

Then one line per message or event, in the order they appeared on the wire:

```json
{"t": 1.565, "conn": 1, "event": "open"}
{"t": 1.565, "conn": 1, "dir": "send", "data": "*IDN?"}
{"t": 1.565, "conn": 1, "dir": "recv", "data": "scpi-sim,DMM,SIM00001,0.5.0"}
{"t": 1.611, "conn": 1, "event": "close", "by": "client"}
```

| Field | Meaning |
| --- | --- |
| `t` | seconds since the recorder started |
| `conn` | connection number from 1; each client connection has its own |
| `dir` | `send` (client to instrument) or `recv` (instrument to client) |
| `data` | the message text without terminator |
| `term` | the terminator, only if it is not `\n` (for example `"\r\n"`) |
| `b64` | base64 instead of `data` for IEEE 488.2 binary blocks (`#<n><len><bytes>` and `#0...`), in both directions |
| `event` | `open` or `close`; a close has `by`: `client`, `target`, or `recorder` (the recorder was stopped with Ctrl+C while a client was connected) |
| `warning` | a parse problem; the bytes were forwarded anyway |

A `\n` outside a binary block always ends a message. A binary block starts
with `#` at the start of a message or after a space, tab or comma, so a `#` inside
a word (`SER AB#15`) is plain text. `\r\n` and `\n` both
count as terminators.

**Exchanges.** When loading, messages are grouped per `send`. A message is a
query if one of its `;`-separated units has a `?` in its header (outside quoted
strings). A query takes the next `recv` on the same connection as its answer.
A query with no answer before the next send or the close is stored as "no
answer", which is valid (real instruments do that on errors). A `recv` that does
not follow a query is "unsolicited": `show` prints it, `serve` and `check` ignore it.

**Command normalisation** (matching in `serve` and in rule patterns): trim,
collapse whitespace, uppercase the header, and reduce every header node to its
SCPI short form. `MEASure:VOLTage:DC? 10` and `meas:volt:dc?  10` both become
`MEAS:VOLT:DC? 10`.

## Rules

By default `check` splits answers at commas (outside quotes). Two fields are
equal if both are numbers with the same value (`+1.000E+00` equals `1`) or the
same text after trimming. Binary blocks must match byte for byte, and a
missing answer on one side must be missing on the other.

A TOML file adapts that per command. Rules are tried in order, the first match
wins; `match` is a glob on the normalised command.

```toml
[[rule]]
match = "*IDN?"
compare = "ignore"      # serial number and firmware differ from unit to unit

[[rule]]
match = "READ?"
abs = 0.05              # |actual - recorded| <= abs + rel * |recorded|
rel = 0.001
```

| `compare` | meaning |
| --- | --- |
| `value` (default) | field by field; numbers with optional `abs` / `rel` tolerance |
| `ignore` | never fails, also if the answer is missing |
| `exact` | the answer text must be identical |
| `regex` | the actual answer must match `pattern` (the recording is only shown) |
| `length` | only the length must match, for binary or long answers |

[`examples/rules.toml`](examples/rules.toml) fits the noisy simulated DMM.

## pytest plugin

The plugin registers itself through the `pytest11` entry point and provides the
`scpi_cassette` fixture:

```python
def test_reads_voltage(scpi_cassette):
    server = scpi_cassette("tests/cassettes/dmm.scpi.jsonl", mode="strict")
    assert my_driver.read_voltage(server.resource) == pytest.approx(12.03, abs=0.01)
```

- `server.resource` is `TCPIP::127.0.0.1::<port>::SOCKET`; `server.host` and
  `server.port` are available too. Relative paths are relative to the pytest rootdir.
- At teardown the test fails if the server saw unknown queries or mismatches,
  or in strict mode if recorded exchanges were never reached (switch that off
  with `require_all=False`).
- `pytest --scpi-record --scpi-target 192.168.1.50` makes the fixture record
  instead of replay, writing the cassette to the given path. That refreshes
  cassettes when the hardware is at hand. `target=` in the call works too.

A complete example is in [`examples/test_with_cassette.py`](examples/test_with_cassette.py).

## Python API

```python
from scpi_replay import Cassette, Recorder, ReplayServer, check

cassette = Cassette.load("dmm.scpi.jsonl")
with ReplayServer(cassette, mode="lookup") as server:      # free port by default
    run_my_test(server.resource)
assert not server.problems

with Recorder(("192.168.1.50", 5025), "new.scpi.jsonl") as rec:
    run_my_test(rec.resource)

report = check(cassette, ("127.0.0.1", 5025), rules="rules.toml", timing="fast")
assert report.ok, report.text()
```

The servers run on a background thread with their own event loop. They are
silent by default; pass `log=print` (any callable taking a string) to
`ReplayServer` or `Recorder` to see problems and warnings as they happen.
`check()` and `ReplayServer` raise `ValueError` for a `conn` that is not in the
cassette.

## Limits

- Raw-socket SCPI only (port 5025 style). No VXI-11 or HiSLIP proxying, no
  serial (RS-232 / USB-CDC) recording yet.
- Replay is a lookup, not a simulation: the cassette does not model instrument
  state. Use [scpi-sim](https://github.com/steitec/scpi-sim) when state matters.
- Arguments are matched as written (only whitespace is collapsed), so
  `OUTP on` does not match `OUTP ON`. Optional nodes such as `[SOURce:]` are
  not resolved.
- `check` cannot tell a late answer from the next one: an answer that arrives
  after its wait has ended but after the next query was sent is taken as the
  answer to that query. Answers that are already there before a command is sent
  are discarded. Raise `--timeout` for slow instruments.
- No SRQ, status-byte reads or device clear (these are not part of a raw socket).
- No cassette editing tools, no GUI.
- No redaction: a cassette contains everything that was sent, including serial
  numbers. Look before you publish one.
- The recorder forwards bytes first and parses next to it. A stream that cannot
  be parsed is still forwarded; the cassette then holds what could be parsed
  plus a `warning`.

## Development

```bash
pip install -e ".[dev]"
pytest -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Part of the steitec toolbox for test
benches, with [scpi-sim](https://github.com/steitec/scpi-sim) and
[test-protocol](https://github.com/steitec/test-protocol).

## License

MIT, see [LICENSE](LICENSE).
