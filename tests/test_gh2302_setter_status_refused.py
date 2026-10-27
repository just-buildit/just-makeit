"""gh-2302: a setter whose C status the binding would discard is refused.

gh-2182 made a refusing setter declarable: ``error`` on a writable property
gives an ``int`` setter whose return the binding tests. The converse went
unchecked. With no ``error`` the binding calls the setter as a bare
statement, so when the header already declares it returning ``int`` the
status is dropped: the assignment succeeds in Python while C refused the
value. doppler had nine such setters, written before gh-2182.

`apply` (and so `status --check`, which replays it) now refuses that before
anything is written, naming the property and the declaration. Each shape
below is one the refusal must see:

* a standalone object, whose header `apply` would otherwise rewrite to
  ``void``, out of step with its definition;
* a module object, whose header is only ever added to (gh-627), so the
  ``int`` stays and the binding drops it silently; its getter is an
  ``expr`` (doppler's ``AccTrace.alpha``), which backs the getter alone, so
  the setter is still the C call;
* a prototype wrapped across lines, the GNU layout, which only the
  multi-line half of the by-name lookup finds.

And the way out: declaring ``error`` makes the same header right.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _incpath as INC

_INT_SETTER = re.compile(r"^void (\w+_set_alpha)\(", re.M)


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr


def _project(tmp_path: Path, *, module: bool) -> Path:
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    if module:
        _ok("module", "dsp", cwd=root)
        _ok("object", "acc", "--module", "dsp", cwd=root)
    else:
        _ok("object", "acc", cwd=root)
    _ok("property", "acc", "alpha", "--type", "double", "--writable", cwd=root)
    return root


def _author_int_setter(root: Path, *, wrapped: bool = False) -> Path:
    """Declare the setter returning ``int``, as an author's status would."""
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    new, n = _INT_SETTER.subn(
        r"int\n\1(" if wrapped else r"int \1(", text, count=1
    )
    assert n == 1, text
    header.write_text(new, encoding="utf-8")
    return header


def _refused(root: Path) -> str:
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    return r.stderr


def _manifest_with(root: Path, line: str) -> None:
    """Add *line* to the one TOML file holding the alpha property."""
    (path,) = [
        p
        for p in [root / "just-makeit.toml", *root.glob("*/*.toml")]
        if 'name = "alpha"' in p.read_text(encoding="utf-8")
    ]
    text = path.read_text(encoding="utf-8")
    anchor = 'name = "alpha"\n'
    assert text.count(anchor) == 1, text
    path.write_text(
        text.replace(anchor, anchor + line + "\n"), encoding="utf-8"
    )


def test_standalone_int_setter_is_refused_before_any_write(tmp_path) -> None:
    root = _project(tmp_path, module=False)
    header = _author_int_setter(root)
    before = header.read_bytes()
    err = _refused(root)
    assert "`acc.alpha` is writable and declares no `error`" in err, err
    assert "p_acc_set_alpha()" in err and "returning `int`" in err, err
    # Refused, not rewritten: the header is the author's evidence.
    assert header.read_bytes() == before
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode != 0
    assert "`acc.alpha` is writable" in r.stderr, r.stderr


def test_module_object_with_expr_getter_is_refused(tmp_path) -> None:
    root = _project(tmp_path, module=True)
    _manifest_with(root, 'expr = "self->handle->alpha"')
    _author_int_setter(root)
    err = _refused(root)
    assert "`acc.alpha` is writable and declares no `error`" in err, err


def test_a_wrapped_prototype_is_read(tmp_path) -> None:
    root = _project(tmp_path, module=True)
    _author_int_setter(root, wrapped=True)
    err = _refused(root)
    assert "returning `int`" in err, err


def test_declaring_error_is_the_way_out(tmp_path) -> None:
    root = _project(tmp_path, module=True)
    _author_int_setter(root)
    _refused(root)
    _manifest_with(root, 'error = "ValueError"')
    _ok("apply", cwd=root)


@pytest.mark.parametrize(
    "spelling",
    [
        "void",
        "static inline void",
        "void JM_HOT",
        "void __attribute__((nonnull))",
        # gh-2314: a comment between the type and the name is not the type.
        "void /* x */",
        "void /* nothing\n   to report */",
        "/* status: none */ void",
        "void // nothing to report\n",
        "/** Set alpha; returns nothing. */\nvoid /* x */\n",
    ],
)
def test_a_void_setter_is_not_refused(tmp_path, spelling: str) -> None:
    """The negative control: a setter that returns nothing has nothing to
    discard, however its declaration is decorated."""
    root = _project(tmp_path, module=True)
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    new, n = _INT_SETTER.subn(spelling + r" \1(", text, count=1)
    assert n == 1, text
    header.write_text(new, encoding="utf-8")
    _ok("apply", cwd=root)


def test_a_pointer_returning_setter_is_refused(tmp_path) -> None:
    """``void *`` returns something, so it is not the ``void`` above."""
    root = _project(tmp_path, module=True)
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    new, n = _INT_SETTER.subn(r"void *\1(", text, count=1)
    assert n == 1, text
    header.write_text(new, encoding="utf-8")
    assert "returning `void *`" in _refused(root)


def test_prose_above_a_wrapped_prototype_is_not_its_type(tmp_path) -> None:
    """A doc comment's prose is not part of the declaration it documents.

    A wrapped prototype is found by the multi-line lookup, whose match can
    start at a prose line of the comment above it; the word ``void`` there
    must not make an ``int`` setter read as one.
    """
    root = _project(tmp_path, module=True)
    header = INC.core_h(root, "acc")
    text = header.read_text(encoding="utf-8")
    new, n = _INT_SETTER.subn(
        "/* Set alpha.\n   Returns void-like 0 when unchanged */\nint\n\\1(",
        text,
        count=1,
    )
    assert n == 1, text
    header.write_text(new, encoding="utf-8")
    assert "returning `int`" in _refused(root)
