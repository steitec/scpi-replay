"""Transcript view of a cassette."""

from __future__ import annotations

from .cassette import Cassette, Event, Message


def transcript(cassette: Cassette, conn: int | None = None, times: bool = False,
               color: bool = False) -> str:
    """Render ``>`` sends, ``<`` receives, connection events and warnings, one per line."""
    def paint(code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color else text

    lines = []
    for it in cassette.items:
        is_warning = isinstance(it, Event) and it.kind == "warning"
        if conn is not None and it.conn != conn and not is_warning:
            continue
        prefix = f"{it.t:7.3f} " if times else ""
        if isinstance(it, Message):
            body = f"<binary {len(it.raw)} bytes>" if it.binary else it.text
            if it.dir == "send":
                lines.append(prefix + paint("36", ">") + " " + body)
            else:
                lines.append(prefix + paint("32", "<") + " " + body)
        elif it.kind == "warning":
            lines.append(prefix + paint("33", f"!! {it.text}"))
        else:
            text = f"-- conn {it.conn} {it.kind}"
            if it.kind == "close" and it.by:
                text = f"-- conn {it.conn} closed by {it.by}"
            elif it.kind == "close":
                text = f"-- conn {it.conn} closed"
            lines.append(prefix + paint("2", text))
    return "\n".join(lines) + ("\n" if lines else "")
