"""gh-1219: the impl provenance marker must survive the project's formatter.

`_impl_marker` emitted the comment as one 132-plus-character line, and
`_patch_step_impls` writes it into an existing `<comp>_core.h`. Headers under
`native/inc/**` are deliberately excluded from `c_format_command` (gh-493:
splice-patching is whitespace-sensitive), so the one file jm had just edited
was the one file its formatter never saw.

A project with a `ColumnLimit` below that length then had a permanently STALE
header: clang-format wrapped the comment, `jm apply` wrote the long line back,
and each md5 returned to the other's. It landed in the count `--check` gates
on, so a correctly-configured project could never reach a clean `jm status`.

**Why wrapping and not formatting.** The issue offered both. Running
`c_format_command` over headers `_patch_step_impls` touched is ruled out by the
exclusion above — that rule exists because reformatting a header makes a later
`apply` believe a declaration moved. Wrapping needs no formatter at all: the
text is a fixed string plus a short relative path, so the result is
deterministic.

**Why wrapping is stable rather than the other end of the same cycle.**
Measured before it was written: clang-format never JOINS a wrapped block
comment — across `ReflowComments` true/Always/false at `ColumnLimit`
80/100/120/0, a two-line comment stayed two lines every time. Only the
unwrapped form is unstable, and only below its own length. So the wrapped form
is a fixed point.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import (  # noqa: E402
    _MARKER_COLS,
    _MARKER_LINE_RE,
    _wrap_c_comment,
)
from just_makeit._impl import _indent4  # noqa: E402

_NO_CLANG_FORMAT = shutil.which("clang-format") is None

LONG = (
    "jm: body sourced from [counter] impl/impl_file in "
    "objects/counter.toml — edit there, not here; "
    "`jm apply` overwrites this."
)


def test_every_line_fits_once_indented():
    """`_indent4` adds four spaces to each line, so the budget is the whole
    rendered width — measuring the unindented string would pass at a width the
    file never actually has."""
    for line in _indent4(_wrap_c_comment(LONG)).splitlines():
        assert len(line) <= _MARKER_COLS, f"{len(line)} cols: {line!r}"


def test_it_is_a_valid_c_block_comment():
    out = _wrap_c_comment(LONG)
    assert out.startswith("/* ")
    assert out.rstrip().endswith("*/")
    for cont in out.splitlines()[1:]:
        assert cont.startswith(" * "), cont
    assert out.count("/*") == 1 and out.count("*/") == 1


def test_a_pathological_path_still_produces_one_comment():
    """A single unbreakable token longer than the width cannot be wrapped by
    anyone, so the only requirement is that the output stays a well-formed
    comment rather than losing its terminator."""
    out = _wrap_c_comment("jm: " + "x" * 200)
    assert out.startswith("/* ") and out.rstrip().endswith("*/")
    assert out.count("*/") == 1


def test_the_stripper_matches_the_wrapped_marker():
    """The peer that has to move with the wrap.

    `_MARKER_LINE_RE` removes the marker before `apply` compares the on-disk
    body against the manifest. Its `.*?` stops at a newline without DOTALL, so
    a wrapped marker would survive the strip and be read as author code that
    changed — turning jm's own comment into a false "someone edited this"
    warning.
    """
    body = _indent4(_wrap_c_comment(LONG)) + "\n    return x;\n"
    assert "jm: body sourced" in body
    stripped = _MARKER_LINE_RE.sub("", body, count=1)
    assert "jm: body sourced" not in stripped
    assert "return x;" in stripped


def test_the_stripper_still_matches_a_legacy_one_line_marker():
    """A project written by an older jm has the unwrapped form on disk, and the
    first `apply` after upgrading must still recognise it — otherwise everyone
    upgrading gets the spurious drift warning once."""
    legacy = (
        "    /* jm: body sourced from [c] impl/impl_file in o/c.toml —"
        " edit there, not here; `jm apply` overwrites this. */\n"
        "    return x;\n"
    )
    stripped = _MARKER_LINE_RE.sub("", legacy, count=1)
    assert "jm: body sourced" not in stripped
    assert "return x;" in stripped


@pytest.mark.skipif(_NO_CLANG_FORMAT, reason="needs clang-format")
@pytest.mark.parametrize("limit", ["80", "100", "120", "0"])
def test_clang_format_leaves_the_wrapped_marker_alone(
    tmp_path: Path, limit: str
):
    """The property the whole fix rests on, checked against the real formatter
    at the column limits a project plausibly sets."""
    (tmp_path / ".clang-format").write_text(
        f"BasedOnStyle: GNU\nColumnLimit: {limit}\n", encoding="utf-8"
    )
    marker = _indent4(_wrap_c_comment(LONG))
    src = tmp_path / "t.c"
    src.write_text(
        f"void f(void)\n{{\n{marker}\n    return;\n}}\n", encoding="utf-8"
    )
    out = subprocess.run(
        ["clang-format", str(src)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    ).stdout

    def comment(text: str) -> list[str]:
        return [
            ln.strip()
            for ln in text.splitlines()
            if "jm:" in ln or ln.strip().startswith("*")
        ]

    assert comment(out) == comment(src.read_text(encoding="utf-8")), (
        f"clang-format rewrote the marker at ColumnLimit {limit}"
    )


@pytest.mark.skipif(_NO_CLANG_FORMAT, reason="needs clang-format")
def test_the_unwrapped_form_is_what_was_unstable(tmp_path: Path):
    """The control. Without it, the test above passes for a marker that was
    never at risk, and would keep passing if the wrap were removed at some
    width that happens to fit."""
    (tmp_path / ".clang-format").write_text(
        "BasedOnStyle: GNU\nColumnLimit: 80\n", encoding="utf-8"
    )
    one_line = "    /* " + LONG + " */"
    src = tmp_path / "u.c"
    src.write_text(
        f"void f(void)\n{{\n{one_line}\n    return;\n}}\n", encoding="utf-8"
    )
    out = subprocess.run(
        ["clang-format", str(src)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    ).stdout
    assert one_line not in out, (
        "the long form survived ColumnLimit 80 — the repro no longer "
        "reproduces, so the wrapped-form test above proves nothing"
    )
