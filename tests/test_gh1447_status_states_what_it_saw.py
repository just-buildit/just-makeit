"""gh-1447: the default bucket asserted a cause nobody established.

`status` sorted an unreconciled fragment into ACTIONABLE / APPLY FIXES
THESE / AUTHOR-OWNED, and AUTHOR-OWNED was the **else** branch -- neither
in `unreconciled_reasons` nor in `refreshable`. Its text then said:

    AUTHOR-OWNED (1) — these differ because you wrote them that way.
    Nothing to do; they stay unreconciled permanently.

So every jm change without a marker on one of the four axes was reported to
its victim as their own handwriting. Measured on doppler: of 43 fragments
filed that way, **25 contained no hand-written code at all** -- re-rendered
and the suite stayed green. 37 more the week before. 62 fragments a
maintainer was told, in as many words, to leave alone.

This is the same defect as gh-1432's mislabel, one report over: a sentence
that names a cause the code never checked. The fix is the same shape --
say what was observed.

GATE: `status` never tells the author they wrote something jm cannot show
      they wrote.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli

from just_makeit import _docsync


def _module_project(tmp_path: Path) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "demo", cwd=root).returncode == 0
    proj = root / "demo"
    assert run_cli("module", "dsp", cwd=proj).returncode == 0
    assert (
        run_cli(
            "object",
            "blk",
            "--module",
            "dsp",
            "--no-state",
            "--no-step",
            "--init-param",
            "n:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "method",
            "blk",
            "execute",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=proj,
        ).returncode
        == 0
    )
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _age_the_fragment(proj: Path) -> Path:
    """Revert one wrapper to the bare call an older jm emitted.

    doppler's repro. Nothing here is hand-written -- it is jm's own output
    from an earlier release, which is the whole point.
    """
    frag = proj / "native" / "src" / "dsp" / "dsp_ext_blk.c"
    s = frag.read_text()
    s2 = re.sub(
        r"if \(PyArray_SetBaseObject\(([^;]*?)\) < 0\) \{[^}]*\}",
        r"PyArray_SetBaseObject(\1);",
        s,
        count=1,
        flags=re.S,
    )
    assert s2 != s, "fixture no longer contains the checked call"
    frag.write_text(s2)
    return frag


class TestItDoesNotClaimTheAuthorWroteIt:
    def test_the_bucket_states_an_absence(self, tmp_path):
        proj = _module_project(tmp_path)
        _age_the_fragment(proj)
        out = run_cli("status", cwd=proj).stdout
        assert "you wrote them that way" not in out, out
        assert "UNEXPLAINED" in out, out
        assert "cannot tell why" in out, out

    def test_it_prints_what_it_observed(self, tmp_path):
        """The counts doppler had to write a script to get."""
        proj = _module_project(tmp_path)
        _age_the_fragment(proj)
        out = run_cli("status", cwd=proj).stdout
        assert "units:" in out, out
        assert "identical" in out, out
        # ...and NAMES the unit that differs, which is the triage.
        assert "1 differ" in out, out
        assert "fn:BlkObj_execute" in out, out


class TestTheUnitDiffIsAboutCodeOnly:
    def test_a_comment_or_a_reflow_is_not_a_difference(self):
        a = "static PyObject *\nw(PyObject *s)\n{\n  return NULL; /* x */\n}\n"
        b = "static PyObject *\nw(PyObject *s)\n{\n    return NULL;\n    /* y */\n}\n"
        d = _docsync.fragment_unit_diff(a, b)
        assert d.differing == (), d
        assert d.identical == ("fn:w",), d

    def test_a_hand_only_unit_is_only_here(self):
        a = "static PyObject *\nw(PyObject *s)\n{\n  return NULL;\n}\n"
        b = a + "static PyObject *\nextra(PyObject *s)\n{\n  return NULL;\n}\n"
        assert _docsync.fragment_unit_diff(b, a).only_here == ("fn:extra",)
        assert _docsync.fragment_unit_diff(a, b).only_rendered == ("fn:extra",)

    def test_a_changed_body_differs(self):
        a = "static PyObject *\nw(PyObject *s)\n{\n  return NULL;\n}\n"
        b = "static PyObject *\nw(PyObject *s)\n{\n  return Py_None;\n}\n"
        assert _docsync.fragment_unit_diff(a, b).differing == ("fn:w",)
