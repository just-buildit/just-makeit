"""
_color.py — minimal ANSI color helpers.

Respects NO_COLOR (https://no-color.org) and falls back to plain text
when stdout is not a TTY.
"""

import os
import sys


def _enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def cmd(text: str) -> str:
    """Return *text* formatted as a bold cyan command, or plain if color is off."""
    if _enabled():
        return f"\033[1;36m{text}\033[0m"
    return text


def done(text: str) -> str:
    """Return *text* formatted as bold green, or plain if color is off."""
    if _enabled():
        return f"\033[1;32m{text}\033[0m"
    return text
