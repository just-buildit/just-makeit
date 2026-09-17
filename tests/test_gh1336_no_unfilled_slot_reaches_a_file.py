"""No generated file may carry an unfilled template slot (gh-1336).

`_init._write` refuses to write a rendered file that still carries an
unmatched ``<<key>>``. That refusal is a genuinely good diagnostic -- gh-1328
found a missing `create_name` because the suite went red with the file and
the slot named, and the fix was then obvious.

**The app writer had no such guard.** The same missing slot produced
valid-looking C with the raw token in it::

    fprintf(stderr, "error: /*<<create_name>>*/() failed\\n");

It compiles -- the token is inside a string literal -- and ships jm's
template syntax in a user-facing message. Nothing failed and nothing was
reported; it surfaced only because an unrelated assertion in
`test_gh944_reserved_identifiers.py` happened to look for the literal
``"_create() failed"`` nearby.

The value of the guard is that a slot added to a template is
**self-reporting**: any render path that forgets to fill it fails loudly the
moment it is added. That property holds for exactly as long as EVERY writer
has the guard, and which writers do is not something the person adding a slot
can be expected to know.

**Why the sweep, and not just a test for the app face.** Fixing the instance
is what gh-1328 nearly did. A check written from one instance encodes that
instance's shape, so this walks every file a project can contain, across the
faces that render them -- a project is scaffolded by RUNNING jm rather than
by listing what it should emit, so a face added later is covered without
being registered anywhere.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R  # noqa: E402
from just_makeit._app import run as app_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

TEXT_SUFFIXES = {
    ".c",
    ".h",
    ".py",
    ".pyi",
    ".txt",
    ".toml",
    ".md",
    ".cmake",
    ".in",
    ".mk",
    ".cfg",
}


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _offenders(root: Path) -> dict:
    """Every generated file carrying a slot nothing filled.

    `R.unfilled_slots` is the same predicate `_init._write` refuses on, so
    this cannot disagree with the guard about what counts -- in particular
    the deliberate `<<IMPLEMENT: ...>>` / `<<MANUAL_STUB>>` markers are
    output, not slots, and pass through both.
    """
    bad = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        if "/build/" in p.as_posix() or "/.git/" in p.as_posix():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        left = R.unfilled_slots(text)
        if left:
            bad[str(p.relative_to(root))] = sorted(left)
    return bad


@pytest.fixture
def wide_project(tmp_path) -> Path:
    """One project exercising as many render faces as cheaply as possible."""
    root = tmp_path / "p"
    _silent(new_run, "p", root)

    # a standalone object, with a create_fn -- the shape that surfaced this
    _silent(
        object_run,
        root,
        "widget",
        None,
        state_vars=[("n", "size_t", "4")],
        create_fn="widget_open",
    )
    _silent(
        method_run,
        root,
        "widget",
        "tweak",
        None,
        "float",
        "float",
        False,
        [],
    )
    _silent(property_run, root, "widget", "gain", None, "double", True)

    # a module object, and a view over it
    _silent(module_run, root, "mod")
    _silent(object_run, root, "acq", "mod", state_vars=[("n", "size_t", "4")])
    _silent(view_run, root, "acq", "Slice", "mod", "acq_create_slice")

    # a module-level function
    _silent(function_run, root, "scale", "mod", return_type="float")

    # every app face -- this is the writer that had no guard
    _silent(app_run, root, target="c", object_="widget")
    _silent(app_run, root, target="console", object_="widget")
    _silent(app_run, root, target="pep723", object_="widget")

    _silent(apply_run, root)
    return root


class TestNoSlotReachesAnyFile:
    def test_the_sweep_is_armed(self, wide_project):
        """A sweep that walks nothing passes trivially. Require both a
        healthy file count and that the app face actually got written --
        the face this issue is about is the one most likely to go missing
        from the fixture."""
        seen = [
            p
            for p in wide_project.rglob("*")
            if p.is_file() and p.suffix in TEXT_SUFFIXES
        ]
        assert len(seen) > 25, f"only {len(seen)} files — fixture too thin"
        apps = list((wide_project / "native/src/app").glob("*.c"))
        assert apps, "the app face is missing from the fixture"

    def test_no_generated_file_carries_a_slot(self, wide_project):
        bad = _offenders(wide_project)
        assert not bad, f"unfilled template slots reached disk: {bad}"


class TestTheAppFaceIsGuardedAtTheWriter:
    """The sweep proves the OUTPUT is clean today. This proves the writer
    would refuse, which is what keeps it clean for a slot added tomorrow."""

    def test_the_app_writer_refuses_an_unfilled_slot(self, tmp_path):
        from just_makeit._init import _write

        target = tmp_path / "x.c"
        with pytest.raises(ValueError, match="nothing filled"):
            _write(target, 'fprintf(stderr, "/*<<never_filled>>*/");\n')
        assert not target.exists(), "it refused and wrote the file anyway"

    def test_the_deliberate_markers_still_pass_through(self, tmp_path):
        """`<<IMPLEMENT: ...>>` is output, not a slot. A guard that refused
        it would refuse every scaffold jm writes."""
        from just_makeit._init import _write

        target = tmp_path / "y.c"
        _silent(_write, target, "/* <<IMPLEMENT: the body >> */\n")
        assert target.exists()
