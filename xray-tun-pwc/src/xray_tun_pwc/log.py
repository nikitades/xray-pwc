"""Log lines shared by every VpnChecker service, on stderr:

    LEVEL event key=value key="value with spaces"

The time and the service name are added in front by whatever collects the
output (s6 on a probe), so the program only writes what it knows.
"""

import json
import sys
from typing import Any

DEBUG, INFO, WARN, ERROR = "DEBUG", "INFO", "WARN", "ERROR"
LEVELS = frozenset({DEBUG, INFO, WARN, ERROR})


def value(raw: Any) -> str:
    text = str(raw).lower() if isinstance(raw, bool) else str(raw)
    if not text or any(c in text for c in ' ="\t\n\r'):
        return json.dumps(text, ensure_ascii=False)
    return text


def line(level: str, event: str, **fields: Any) -> str:
    return " ".join([level, event, *(f"{key}={value(raw)}" for key, raw in fields.items())])


def log(level: str, event: str, **fields: Any) -> None:
    print(line(level, event, **fields), file=sys.stderr, flush=True)


def relayed(child: str, raw: str) -> str:
    """A line of a child process, with the child's name in front of its event:
    "WARN link_rejected ..." from xtc becomes "WARN xtc.link_rejected ...".
    Anything else is kept whole as a message."""
    text = raw.rstrip("\r\n")
    level, _, rest = text.partition(" ")
    if level in LEVELS and rest:
        return f"{level} {child}.{rest}"
    return line(WARN, f"{child}.output", msg=text)
