from __future__ import annotations

import pytest

from scpi_replay.scpi import is_query, normalize, parse_number, split_fields


@pytest.mark.parametrize(
    "text, expected",
    [
        ("*IDN?", True),
        ("CONF:VOLT 10;READ?", True),
        ("OUTP ON", False),
        ('DISP:TEXT "Ready?"', False),
        ('DISP:TEXT "a;b?"', False),
        ("SYST:ERR?", True),
        ("VOLT? MAX", True),
    ],
)
def test_is_query(text, expected):
    assert is_query(text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("MEASure:VOLTage:DC? 10", "MEAS:VOLT:DC? 10"),
        ("meas:volt:dc?   10", "MEAS:VOLT:DC? 10"),
        ("  *idn? ", "*IDN?"),
        ("OUTPut1:STATe ON", "OUTP1:STAT ON"),
        ("TRIGger:SOURce IMMediate", "TRIG:SOUR IMMediate"),
        ("CONF:VOLT 10;READ?", "CONF:VOLT 10;READ?"),
        ("CONFigure:VOLTage 10;READ?", "CONF:VOLT 10;READ?"),
        ("SENSe:VOLTage", "SENS:VOLT"),
        ("CALCulate", "CALC"),
        ("FREQuency", "FREQ"),
        ("POWer", "POW"),
        ("VOLT", "VOLT"),
        ("DC", "DC"),
        ("*RST", "*RST"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected


def test_normalize_keeps_semicolon_in_string():
    assert normalize('DISPlay:TEXT "a;b"') == 'DISP:TEXT "a;b"'


def test_split_fields():
    assert split_fields('+1.0E+00,"a,b",3') == ["+1.0E+00", '"a,b"', "3"]
    assert split_fields("") == [""]
    assert split_fields("'x,y',\"a\"\"b,c\"") == ["'x,y'", '"a""b,c"']


@pytest.mark.parametrize(
    "field, expected",
    [("+1.000E+00", 1.0), (" 12 ", 12.0), ("9.9E37", 9.9e37), ("-.5", -0.5)],
)
def test_parse_number(field, expected):
    assert parse_number(field) == expected


@pytest.mark.parametrize("field", ["ON", "nan", "inf", "1_0", "", "0x10", "1e999"])
def test_parse_number_rejects(field):
    assert parse_number(field) is None
