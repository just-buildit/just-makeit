"""gh-1604: a ``#`` inside a bracket argument or a multi-line quoted string is
text, not a comment.

`apply` decides whether to rewrite the root CMakeLists's managed install block
by comparing its CMake commands (:func:`_rootcmake.calls`) with the render's
(gh-1589). The comment stripper under that comparison knew bracket COMMENTS
and single-line quotes, but not bracket ARGUMENTS, and it reset its in-quote
state at every line. The block already carries ``install(CODE [[ ... ]])``,
so a later template change after a ``#`` inside it compared equal to the old
text, and `apply` never delivered it to an existing project. The same parser
reads the ``ROOT CMAKE`` rows, so they share the fix.

GATE: a ``#`` inside a bracket argument or a quoted string that spans lines
      is kept as text by the root-CMakeLists command reader, a parenthesis
      inside either does not end the call, and `apply` delivers a template
      change to the managed install block that follows such a ``#``.
"""

from __future__ import annotations


from _jmrun import run_cli
from just_makeit import _apply
from just_makeit import _rootcmake as R


def test_a_change_after_a_hash_in_a_bracket_argument_is_a_change():
    a = 'install(CODE [[\nfile(WRITE x "a")  # v1\n]])\n'
    b = 'install(CODE [[\nfile(WRITE x "a")  # v2 changed\n]])\n'
    assert R.calls(a) != R.calls(b)


def test_a_hash_line_inside_a_multiline_quote_is_kept():
    c = 'file(WRITE x "line1\n# prefix=/old\n")\n'
    assert R.calls(c) == [
        R.Call("file", ("WRITE", "x", "line1\n# prefix=/old\n"))
    ]


def test_a_parenthesis_inside_a_literal_does_not_end_the_call():
    text = 'install(CODE [==[ ) ]] ]==])\nx("(" [[)]])\nset(X 1)\n'
    assert [c.name for c in R.calls(text)] == ["install", "x", "set"]
    assert R.calls(text)[0].args == ("CODE", " ) ]] ")


def test_comments_outside_literals_are_still_dropped():
    text = 'set(A 1) # set(B 2)\n#[[ set(C 3)\n]] set(D "#4")\n'
    assert R.calls(text) == [
        R.Call("set", ("A", "1")),
        R.Call("set", ("D", "#4")),
    ]


def test_apply_delivers_a_change_after_a_hash_in_the_install_code(tmp_path):
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    fresh = (tmp_path / "p" / "CMakeLists.txt").read_text(encoding="utf-8")
    anchor = 'file(WRITE "${JM_PC_FILE}" "${_jm_pc}")\n'
    assert fresh.count(anchor) == 1

    real = tmp_path / "real.cmake"
    temp = tmp_path / "temp.cmake"
    real.write_text(fresh.replace(anchor, anchor + "# v1\n"), encoding="utf-8")
    temp.write_text(fresh.replace(anchor, anchor + "# v2\n"), encoding="utf-8")

    assert _apply._splice_root_install(real, temp) is True
    assert "# v2\n" in real.read_text(encoding="utf-8")
