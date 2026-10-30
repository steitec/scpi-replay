import builtins

import pytest

from scpi_replay.cassette import Message
from scpi_replay.compare import Rule, RulesError, compare, find_rule, load_rules


def msg(raw: bytes, binary: bool = False) -> Message:
    return Message(0.0, 1, "recv", raw, binary, b"\n")


D = Rule("*")


def test_default_numeric_format_insensitive():
    assert compare(msg(b"+1.000E+00"), b"1", D).status == "PASS"


def test_default_numeric_mismatch_reason():
    v = compare(msg(b"1.0"), b"1.0001", D)
    assert v.status == "FAIL"
    assert "1.0" in v.reason and "1.0001" in v.reason


def test_default_trimmed_text():
    assert compare(msg(b'"ON"'), b'"ON" ', D).status == "PASS"


def test_default_field_count():
    v = compare(msg(b"1,2,3"), b"1,2", D)
    assert v.status == "FAIL" and "field count" in v.reason


def test_default_text_differs():
    assert compare(msg(b"ABC"), b"ABD", D).status == "FAIL"


def test_tolerance_abs():
    r = Rule("*", abs=0.01)
    assert compare(msg(b"12.03"), b"12.035", r).status == "PASS"
    assert compare(msg(b"12.03"), b"12.05", r).status == "FAIL"


def test_tolerance_rel():
    assert compare(msg(b"1000"), b"1000.9", Rule("*", rel=0.001)).status == "PASS"


def test_ignore():
    assert compare(msg(b"A"), b"B", Rule("*", compare="ignore")).status == "IGNORED"


def test_exact():
    r = Rule("*", compare="exact")
    assert compare(msg(b"+1.0"), b"1.0", r).status == "FAIL"
    assert compare(msg(b"+1.0"), b"+1.0", r).status == "PASS"


def test_regex():
    r = Rule("*", compare="regex", pattern=r"^\+?0,")
    assert compare(msg(b'+0,"No error"'), b'+0,"No error"', r).status == "PASS"
    v = compare(msg(b'+0,"No error"'), b'-113,"Undefined header"', r)
    assert v.status == "FAIL" and "no match" in v.reason


def test_length():
    r = Rule("*", compare="length")
    assert compare(msg(bytes(2048), True), bytes(2048), r).status == "PASS"
    v = compare(msg(bytes(2048), True), bytes(1024), r)
    assert v.status == "FAIL" and v.reason == "length 2048 != 1024"
    assert compare(msg(b"abc"), b"xyz", r).status == "PASS"


def test_binary_default():
    assert compare(msg(b"\x00\x01\x02", True), b"\x00\x01\x02", D).status == "PASS"
    v = compare(msg(b"\x00\x01\x02\x03", True), b"\x00\x01\x02\x04", D)
    assert v.status == "FAIL" and "binary differs at byte 3" in v.reason


def test_missing_responses():
    assert compare(None, None, D).status == "PASS"
    v = compare(None, b"x", D)
    assert v.status == "FAIL" and v.reason == "unexpected response"
    v = compare(msg(b"1"), None, D)
    assert v.status == "FAIL" and v.reason == "no response"


def test_find_rule():
    rules = [Rule("*IDN?", compare="ignore"), Rule("MEAS:VOLT*", abs=1), Rule("MEAS*", abs=2)]
    assert find_rule(rules, "*idn?") is rules[0]
    assert find_rule(rules, "MEASure:VOLTage:DC?") is rules[1]
    assert find_rule(rules, "MEAS:CURR?") is rules[2]
    assert find_rule(rules, "FOO?") == Rule("*")
    assert find_rule([Rule("measure:volt*")], "MEAS:VOLT?").match == "measure:volt*"


def write(tmp_path, text):
    p = tmp_path / "rules.toml"
    p.write_text(text)
    return p


def test_load_rules_valid(tmp_path):
    p = write(tmp_path, '''
[[rule]]
match = "*IDN?"
compare = "ignore"

[[rule]]
match = "SYST:ERR?"
compare = "regex"
pattern = '^\\+?0,'

[[rule]]
match = "MEAS*"
abs = 0.01
rel = 1
''')
    rules = load_rules(p)
    assert [r.match for r in rules] == ["*IDN?", "SYST:ERR?", "MEAS*"]
    assert rules[1].pattern == r"^\+?0,"
    assert rules[2].abs == 0.01 and rules[2].rel == 1


@pytest.mark.parametrize("body,msg_part", [
    ('[[rule]]\nmatch="A"\n\n[[rule]]\nmatch="B"\ntol=1\n', "rule 2: unknown key 'tol'"),
    ('[[rule]]\nmatch="A"\ncompare="regex"\n', "rule 1"),
    ('[[rule]]\nmatch="A"\ncompare="regex"\npattern="("\n', "rule 1"),
    ('[[rule]]\nmatch="A"\ncompare="fuzzy"\n', "rule 1"),
    ('[[rule]]\ncompare="ignore"\n', "rule 1"),
    ('[[rule]]\nmatch="A"\nabs=-1\n', "rule 1"),
    ('[[rule]]\nmatch="A"\nabs=true\n', "rule 1"),
    ('[[rule]]\nmatch="A"\nrel="x"\n', "rule 1"),
    ('[[rule]]\nmatch=3\n', "rule 1"),
    ('[[rule]\n', "rules"),
])
def test_load_rules_errors(tmp_path, body, msg_part):
    with pytest.raises(RulesError, match=msg_part):
        load_rules(write(tmp_path, body))


def test_load_rules_missing_file(tmp_path):
    with pytest.raises(RulesError):
        load_rules(tmp_path / "nope.toml")


def test_toml_missing(tmp_path, monkeypatch):
    p = write(tmp_path, "")
    real = builtins.__import__

    def fake(name, *a, **k):
        if name in ("tomllib", "tomli"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RulesError, match=r"pip install scpi-replay\[toml\]"):
        load_rules(p)
