"""gh-1936: a test finds a jm warning by its form, not by a word in a path.

`test_scaffolding_an_override_is_quiet` failed with no warning printed. It
flagged every output line containing ``warning``, and jm prints the path of
every file it writes -- so the TMPDIR of an agent whose branch was named
``...-warning-sweep`` turned it red, and the same tree passed under any
other TMPDIR. Seventeen checks in nine files asked the same question the
same way: the absent ones went red on such a path, and the present ones
passed on the path alone, whether or not jm had warned.

A jm warning is a LINE that opens with the word, as `_report.warn` prints it
(``warning !:`` / ``warning ~:``) or as the emitters that predate it do
(``warning:``, ``WARNING:``). `tests/_jmrun.warning_lines` reads that form,
with the marks taken from `_report`; this file holds it to what `_report`
actually prints, to a real path, and holds the suite to using it.

GATE: no test detects a jm warning by the bare word anywhere in its output;
      it reads the lines with `_jmrun.warning_lines`.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from _jmrun import run_cli, warning_lines
from just_makeit import _report

TESTS = Path(__file__).parent


def _rendered(gates: bool) -> str:
    """One line exactly as `_report.warn` prints it at the given weight."""
    buf = io.StringIO()
    _report.warn("the body", gates=gates, stream=buf)
    return buf.getvalue()


def test_every_weight_report_prints_is_a_warning_line(monkeypatch):
    """The anchor is held to `_report`, the one place a warning's form is
    decided, at both weights and nested in a report block. A new prefix
    there fails here, rather than leaving every caller blind."""
    monkeypatch.setattr(_report, "_gating", 0)  # restored after the test
    for gates in (False, True):
        line = _rendered(gates)
        assert warning_lines(line) == [line.rstrip("\n")], line
        buf = io.StringIO()
        _report.warn("nested", gates=gates, stream=buf, indent="    ")
        assert len(warning_lines(buf.getvalue())) == 1, buf.getvalue()


def test_a_path_naming_the_word_is_not_a_warning(tmp_path):
    """The gh-1936 trigger, on jm's own output: a project under a directory
    named for the word prints the word on every line it writes, and none of
    those lines is a warning."""
    word = _rendered(False).split()[0]
    root = tmp_path / f"{word}-dir" / "p"
    r = run_cli("new", "p", str(root), "--no-c-prefix")
    assert r.returncode == 0, r.stderr
    assert str(root) in r.stdout, "the trap was not armed: no path printed"
    assert warning_lines(r.stdout + r.stderr) == []


def _bare_word_checks(tree: ast.AST, word: str) -> "list[int]":
    """Lines comparing the bare *word* (any case) with ``in`` / ``not in``.

    Read from the AST, so a docstring or comment that QUOTES the shape --
    this file's, for one -- is not a finding. A literal carrying more than
    the word (``"warning !:"``, a mark that is the subject of its test)
    is not one either: a path does not contain it.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
        and node.left.value.lower() == word
        and all(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
    ]


def test_no_test_finds_a_warning_by_the_bare_word():
    """Registration-free: every test file is read, a new one included."""
    word = _rendered(False).split()[0].lower()
    found = [
        f"{path.name}:{line}"
        for path in sorted(TESTS.glob("*.py"))
        for line in _bare_word_checks(
            ast.parse(path.read_text(encoding="utf-8")), word
        )
    ]
    assert not found, (
        f"these detect a jm warning by the word {word!r} anywhere in the "
        "output, and jm prints paths: a TMPDIR or branch holding the word "
        "decides the result (gh-1936). Read the lines with "
        f"`_jmrun.warning_lines` instead: {found}"
    )


def test_the_scan_sees_the_shape_it_refuses():
    """The scan is armed: it finds the gh-1936 spelling, in either sense
    and either case, and passes over a docstring quoting it."""
    src = (
        '"""assert "warning" not in err"""\n'
        'assert "warning" not in err\n'
        'ok = "WARNING" in line.lower()\n'
        'assert "warning !:" in err\n'
    )
    assert _bare_word_checks(ast.parse(src), "warning") == [2, 3]
