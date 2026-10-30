from scpi_replay.framing import Frame, Framer


def test_lines():
    assert Framer().feed(b"*IDN?\nREAD?\r\n") == [
        Frame(b"*IDN?", False, b"\n"),
        Frame(b"READ?", False, b"\r\n"),
    ]


def test_line_split_across_chunks():
    f = Framer()
    assert f.feed(b"MEAS:VO") == []
    assert f.feed(b"LT?\n") == [Frame(b"MEAS:VOLT?", False, b"\n")]


def test_definite_block_with_newline_in_payload():
    assert Framer().feed(b"#15ab\ncd\n") == [Frame(b"#15ab\ncd", True, b"\n")]


def test_block_split_across_reads():
    f = Framer()
    frames = []
    for b in b"#15ab\ncd\n":
        frames += f.feed(bytes([b]))
    assert frames == [Frame(b"#15ab\ncd", True, b"\n")]


def test_block_after_header_in_one_message():
    assert Framer().feed(b"DATA #13xyz\n") == [Frame(b"DATA #13xyz", True, b"\n")]


def test_flush_block_without_trailing_newline():
    f = Framer()
    assert f.feed(b"#13xyz") == []
    assert f.flush() == Frame(b"#13xyz", True, b"")


def test_indefinite_block():
    assert Framer().feed(b"#0abc\n") == [Frame(b"#0abc", True, b"\n")]


def test_hash_in_quoted_string_is_text():
    assert Framer().feed(b'DISP:TEXT "#12"\n') == [
        Frame(b'DISP:TEXT "#12"', False, b"\n")
    ]


def test_flush_returns_leftover():
    f = Framer()
    assert f.feed(b"*RST") == []
    assert f.flush() == Frame(b"*RST", False, b"")
    assert f.flush() is None


def test_bare_hash_digit_does_not_stall():
    assert Framer().feed(b"FOO #5\n") == [Frame(b"FOO #5", False, b"\n")]
    assert Framer().feed(b"#2\n") == [Frame(b"#2", False, b"\n")]
    assert Framer().feed(b"#H\n") == [Frame(b"#H", False, b"\n")]


def test_partial_length_digits_wait():
    f = Framer()
    assert f.feed(b"#31") == []
    assert f.feed(b"02" + b"x" * 102) == []
    assert f.feed(b"\n")[0].binary


def test_stray_quote_does_not_swallow_newlines():
    assert Framer().feed(b"ERR \"can't\nREAD?\n") == [
        Frame(b"ERR \"can't", False, b"\n"),
        Frame(b"READ?", False, b"\n"),
    ]


def test_quote_state_does_not_leak():
    frames = Framer().feed(b"X 'abc\n#13xyz\n")
    assert frames == [Frame(b"X 'abc", False, b"\n"), Frame(b"#13xyz", True, b"\n")]
