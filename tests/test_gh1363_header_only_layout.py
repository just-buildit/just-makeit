"""A header-only core's definitions each start on their own line (gh-1363).

``apply_header_only`` moves the ``_core.c`` definition slots into the sacred
header as ``static inline``. The slots are fragments: ``_core.c``'s template
supplies the line breaks between them. Joined bare, an accessor's return
type landed on the closing brace of ``steps()``::

    }float
    static inline q_get_scale(const q_state_t *state)

which compiles (``float static inline f()`` is legal, obsolescent C), so
``make test`` stayed green on a file the author owns from then on.

The checks are on the header's STRUCTURE, anchored to line starts, across
every shape that empties a different slot -- an empty slot is exactly what
moves which two fragments meet.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: Each shape empties a different definition slot, so each puts a different
#: pair of fragments next to each other.
SHAPES = {
    "step_and_state": dict(
        state_vars=[("scale", "float", "1.0")],
        arg_type="float",
        return_type="int16_t",
    ),
    "no_step": dict(
        state_vars=[("g", "double", "2.0"), ("k", "int", "3")],
        no_step=True,
    ),
    "no_state": dict(no_state=True, arg_type="float", return_type="float"),
    "no_reset": dict(state_vars=[("a", "int", "1")], no_reset=True),
    "complex_default": dict(state_vars=[("g", "double", "1.0")]),
}


def _header(tmp_path: Path, shape: dict) -> list[str]:
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", tmp_path)
        object_run(tmp_path, "q", None, header_only=True, **shape)
    path = tmp_path / "native" / "inc" / "q" / "q_core.h"
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_code_follows_a_closing_brace(tmp_path: Path, shape: str) -> None:
    """A column-0 ``}`` ends its line, bar a typedef's ``} name;``."""
    lines = _header(tmp_path, SHAPES[shape])
    bad = [
        s
        for s in lines
        if s.startswith("}") and not re.fullmatch(r"\};?|\} \w+;", s)
    ]
    assert not bad, bad


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_static_inline_carries_a_return_type(
    tmp_path: Path, shape: str
) -> None:
    """``static inline`` is never followed directly by the function name.

    That is the gh-1363 shape: :func:`staticize` works line by line, so when
    the type sat on the previous line it prefixed the NAME line instead.
    """
    lines = _header(tmp_path, SHAPES[shape])
    typeless = [s for s in lines if re.match(r"static inline \w+\s*\(", s)]
    assert not typeless, typeless
    assert any(s.startswith("static inline") for s in lines)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_definition_is_set_off_by_one_blank_line(
    tmp_path: Path, shape: str
) -> None:
    """One blank line before each definition, never a run of them.

    The template's own ``step()`` sits under its Doxygen comment instead.
    """
    lines = _header(tmp_path, SHAPES[shape])
    for n, s in enumerate(lines):
        if s.startswith("static inline"):
            prev = lines[n - 1]
            assert prev == "" or prev.endswith("*/"), (n, prev, s)
    # The moved definitions start at the inline create(); what is above it
    # is the header template's own layout, not this feature's.
    moved = "\n".join(lines[lines.index("static inline q_state_t *") :])
    assert "\n\n\n" not in moved, moved
