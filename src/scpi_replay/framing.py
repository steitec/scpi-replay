"""Split a SCPI byte stream into messages (pure bytes in, frames out)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    data: bytes  # message without terminator (binary: includes the whole block)
    binary: bool  # message contains a "#<n><len><payload>" or "#0" block
    term: bytes  # b"\n", b"\r\n" or b"" (stream ended without a newline)


class Framer:
    """Incremental message framer.

    A message ends at a newline outside a quoted string or binary block.
    A newline outside a block payload always ends the message; quotes only
    suppress block detection and reset at each message end. Definite blocks (``#<n><len><payload>``) are skipped by length, so
    payload bytes may contain newlines; ``#0`` blocks run to the next newline.
    State is kept across ``feed`` calls and consumed bytes are not rescanned.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0  # scan position in _buf
        self._quote = 0  # active quote byte, or 0
        self._binary = False
        self._indefinite = False
        self._block_end = 0  # end of last definite block in _buf

    def _reset(self) -> None:
        self._quote = 0
        self._binary = False
        self._indefinite = False
        self._block_end = 0

    def _emit_line(self, frames: list[Frame]) -> None:
        """Close the message ending at the newline at ``_pos``."""
        buf = self._buf
        end = self._pos
        term = b"\n"
        if end > self._block_end and end > 0 and buf[end - 1] == 0x0D:
            end -= 1
            term = b"\r\n"
        frames.append(Frame(bytes(buf[:end]), self._binary, term))
        del buf[: self._pos + 1]
        self._pos = 0
        self._reset()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf += chunk
        buf = self._buf
        frames: list[Frame] = []
        while self._pos < len(buf):
            c = buf[self._pos]
            if c == 0x0A:
                self._emit_line(frames)
                continue
            if self._indefinite:
                self._pos += 1
                continue
            if self._quote:
                if c == self._quote:
                    self._quote = 0
                self._pos += 1
                continue
            if c in b"\"'":
                self._quote = c
                self._pos += 1
                continue
            if c == 0x23:  # "#"
                if self._pos + 1 >= len(buf):
                    break  # need the next byte
                d = buf[self._pos + 1]
                if d == 0x30:  # "#0"
                    self._binary = self._indefinite = True
                    self._pos += 2
                    continue
                if 0x31 <= d <= 0x39:
                    n = d - 0x30
                    start = self._pos + 2 + n
                    digits = bytes(buf[self._pos + 2 : start])
                    if digits and not digits.isdigit():
                        self._pos += 1  # not a block header
                        continue
                    if len(digits) < n:
                        break  # only digits so far: wait for the rest
                    self._binary = True
                    self._block_end = start + int(digits)
                    # Skip the payload without scanning it (it may be incomplete).
                    self._pos = self._block_end
                    continue
                self._pos += 1
                continue
            self._pos += 1
        return frames

    def flush(self) -> Frame | None:
        """Return leftover bytes at stream end as a frame with empty terminator.

        The frame is binary if a block header was seen, else text.
        """
        if not self._buf:
            return None
        frame = Frame(bytes(self._buf), self._binary, b"")
        self._buf.clear()
        self._pos = 0
        self._reset()
        return frame
