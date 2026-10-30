"""Response comparison and the TOML rules file used by ``check``."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .cassette import Message
from .scpi import normalize, parse_number, split_fields

_MODES = ("value", "ignore", "exact", "regex", "length")
_KEYS = {"match", "compare", "abs", "rel", "pattern"}


class RulesError(ValueError):
    """The rules file is unreadable or invalid."""


@dataclass(frozen=True)
class Rule:
    match: str
    compare: Literal["value", "ignore", "exact", "regex", "length"] = "value"
    abs: float = 0.0
    rel: float = 0.0
    pattern: str | None = None


@dataclass(frozen=True)
class Verdict:
    status: Literal["PASS", "FAIL", "IGNORED"]
    reason: str = ""


def _toml_loads(text: str) -> dict[str, Any]:
    try:
        import tomllib as toml
    except ImportError:
        try:
            import tomli as toml  # type: ignore[no-redef]
        except ImportError:
            raise RulesError(
                "reading rules files needs TOML support: pip install scpi-replay[toml]"
            ) from None
    try:
        return toml.loads(text)
    except toml.TOMLDecodeError as exc:
        raise RulesError(f"invalid rules file: {exc}") from None


def _number(value: Any, key: str, n: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RulesError(f"rule {n}: '{key}' must be a number >= 0")
    return float(value)


def _parse_rule(raw: Any, n: int) -> Rule:
    if not isinstance(raw, dict):
        raise RulesError(f"rule {n}: must be a table")
    for key in raw:
        if key not in _KEYS:
            raise RulesError(f"rule {n}: unknown key '{key}'")
    match = raw.get("match")
    if not isinstance(match, str) or not match:
        raise RulesError(f"rule {n}: 'match' is required and must be a string")
    mode = raw.get("compare", "value")
    if mode not in _MODES:
        raise RulesError(f"rule {n}: unknown compare '{mode}' (use {', '.join(_MODES)})")
    pattern = raw.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        raise RulesError(f"rule {n}: 'pattern' must be a string")
    if mode == "regex":
        if pattern is None:
            raise RulesError(f"rule {n}: compare = \"regex\" needs a 'pattern'")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RulesError(f"rule {n}: bad regex: {exc}") from None
    return Rule(
        match,
        mode,
        _number(raw.get("abs", 0), "abs", n),
        _number(raw.get("rel", 0), "rel", n),
        pattern,
    )


def load_rules(path: str | Path) -> list[Rule]:
    """Read ``[[rule]]`` tables from a TOML file; invalid input raises RulesError."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"cannot read rules file: {exc}") from None
    data = _toml_loads(text)
    raw_rules = data.get("rule", [])
    if not isinstance(raw_rules, list):
        raise RulesError("'rule' must be an array of tables ([[rule]])")
    return [_parse_rule(r, i) for i, r in enumerate(raw_rules, 1)]


def find_rule(rules: list[Rule], command: str) -> Rule:
    """First rule whose normalised glob matches the normalised command."""
    cmd = normalize(command)
    for rule in rules:
        if fnmatch.fnmatchcase(cmd, normalize(rule.match)):
            return rule
    return Rule("*")


def _compare_value(exp: str, act: str, rule: Rule) -> Verdict:
    ef, af = split_fields(exp.strip()), split_fields(act.strip())
    if len(ef) != len(af):
        return Verdict("FAIL", f"field count {len(ef)} != {len(af)}")
    for e, a in zip(ef, af):
        en, an = parse_number(e), parse_number(a)
        if en is not None and an is not None:
            if abs(an - en) > rule.abs + rule.rel * abs(en):
                return Verdict("FAIL", f"expected {e.strip()}, got {a.strip()}")
        elif e.strip() != a.strip():
            return Verdict("FAIL", f"expected {e.strip()}, got {a.strip()}")
    return Verdict("PASS")


def compare(expected: Message | None, actual: bytes | None, rule: Rule) -> Verdict:
    """Compare a target's response (raw bytes, no terminator) with the recording."""
    if rule.compare == "ignore":
        return Verdict("IGNORED")
    if expected is None:
        return Verdict("PASS") if actual is None else Verdict("FAIL", "unexpected response")
    if actual is None:
        return Verdict("FAIL", "no response")

    if rule.compare == "length":
        if expected.binary:
            e, a = len(expected.raw), len(actual)
        else:
            e, a = len(expected.text), len(actual.decode("utf-8", "replace"))
        return Verdict("PASS") if e == a else Verdict("FAIL", f"length {e} != {a}")

    if expected.binary:
        if expected.raw == actual:
            return Verdict("PASS")
        n = len(expected.raw)
        idx = next((i for i in range(min(n, len(actual))) if expected.raw[i] != actual[i]),
                   min(n, len(actual)))
        return Verdict("FAIL", f"binary differs at byte {idx}")

    text = actual.decode("utf-8", "replace")
    if rule.compare == "exact":
        if text == expected.text:
            return Verdict("PASS")
        return Verdict("FAIL", f"expected {expected.text!r}, got {text!r}")
    if rule.compare == "regex":
        assert rule.pattern is not None
        if re.search(rule.pattern, text):
            return Verdict("PASS")
        return Verdict("FAIL", f"no match for /{rule.pattern}/")
    return _compare_value(expected.text, text, rule)
