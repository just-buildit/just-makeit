"""A sacred fragment predating ``jm_array_arg`` is reported (gh-1734).

gh-1700 routed every generated array-argument conversion through one
helper, ``jm_array_arg``, which reads a byte buffer into a ``uint8_t[]`` /
``int8_t[]`` as its bytes -- and widened the ``.pyi`` beside it. (It also
refused a ``str`` on every array until gh-1824 made that a per-param
``str_hint`` opt-in; for any other element type the helper is now
``PyArray_FROM_OTF``, so only a one-byte array's bare call is reported.)
A sacred ``_ext_<obj>.c`` fragment rendered before that still calls a bare
``PyArray_FROM_OTF``, and
``apply`` only ever ADDS members to one, so the stub and the runtime
disagreed and nothing said so. doppler had ~40 such fragments; ``apply``
named 55 lacking gh-1710's output-size guard and none of these.

The advisory rides the same declared-feature axis as gh-1710's guard
(``_docsync._FEATURE_MARKERS``): the reference render calls the helper, the
fragment does not, so the member is named with the consequence.

GATE: a fragment converting a byte-array argument without ``jm_array_arg``
is named on apply; a current one, a GNU-rewrapped current one, a member
with no array argument and (gh-1824) a bare call on a ``float[]`` are not.
"""

from __future__ import annotations

import re

from _jmrun import run_cli

FRAG = ("native", "src", "m", "m_ext_r.c")
WHY = "through jm_array_arg"

#: One call to the helper, any layout: its four arguments, the last the
#: parameter's name as a string literal.
_CALL_RE = re.compile(
    r"jm_array_arg\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,"
    r"\s*\"[^\"]*\"\s*\)"
)


def _project(tmp_path, *method_args):
    """A module object with one method ``scale`` declared by *method_args*."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    r = run_cli(
        "object", "r", "--module", "m", "--no-state", "--no-step", cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method", "r", "scale", "--module", "m", *method_args, cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _apply(proj) -> str:
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _array_project(tmp_path, ptype="uint8_t[]"):
    return _project(
        tmp_path, "--param", f"w:{ptype}", "--return-type", "float"
    )


def _predate(frag) -> None:
    """Rewrite *frag* as rendered before gh-1700: a bare PyArray_FROM_OTF."""
    old, n = _CALL_RE.subn(
        r"(PyArrayObject *)PyArray_FROM_OTF(\1, \2, \3)", frag.read_text()
    )
    assert n == 1, "fixture no longer renders one jm_array_arg call"
    frag.write_text(old)


def test_a_fragment_predating_jm_array_arg_is_named(tmp_path):
    proj = _array_project(tmp_path)
    _predate(proj.joinpath(*FRAG))

    out = _apply(proj)
    assert "m_ext_r.c" in out, out
    assert f"scale: jm converts a uint8_t[] / int8_t[] argument {WHY}" in out
    assert "read as its bytes" in out, out
    # ...and nothing else is blamed for it.
    assert "result shape" not in out, out


def test_a_bare_call_on_a_wider_element_type_is_silent(tmp_path):
    """gh-1824: for a float[] the helper IS PyArray_FROM_OTF -- a str is
    numpy's without a str_hint -- so the old call has no consequence to
    name, and a fragment is not told to regenerate for nothing."""
    proj = _array_project(tmp_path, "float[]")
    _predate(proj.joinpath(*FRAG))
    out = _apply(proj)
    assert "no longer matches" not in out, out


def test_a_current_fragment_is_silent(tmp_path):
    out = _apply(_array_project(tmp_path))
    assert "no longer matches" not in out, out


def test_a_gnu_rewrapped_call_is_still_the_helper(tmp_path):
    """GNU style spaces the call and a narrow column wraps its arguments;
    the marker is the call, not one layout of it."""
    proj = _array_project(tmp_path)
    frag = proj.joinpath(*FRAG)
    src, n = re.subn(
        r"jm_array_arg\(", "jm_array_arg (\n    ", frag.read_text()
    )
    assert n == 1
    frag.write_text(src)
    out = _apply(proj)
    assert "no longer matches" not in out, out


def test_the_name_in_a_comment_is_not_the_call(tmp_path):
    """Generated C carries prose about itself: the helper named in a
    comment beside a bare conversion must not satisfy the marker."""
    proj = _array_project(tmp_path)
    frag = proj.joinpath(*FRAG)
    old, n = _CALL_RE.subn(
        r"/* was jm_array_arg(w) */ "
        r"(PyArrayObject *)PyArray_FROM_OTF(\1, \2, \3)",
        frag.read_text(),
    )
    assert n == 1
    frag.write_text(old)
    assert WHY in _apply(proj)


def test_a_member_with_no_array_argument_is_silent(tmp_path):
    proj = _project(tmp_path, "--arg-type", "float", "--return-type", "float")
    src = proj.joinpath(*FRAG).read_text()
    assert "jm_array_arg" not in src and "PyArray_FROM_OTF" not in src
    out = _apply(proj)
    assert "no longer matches" not in out, out
