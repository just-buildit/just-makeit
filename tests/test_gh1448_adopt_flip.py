"""gh-1448: `fragment = "generated"` makes a module fragment jm's content.

A standalone object's `<comp>_ext.c` is glue. A module object's
`<mod>_ext_<obj>.c` is the same generated wrapper code and was sacred, so
where a wrapper lived decided whether it received fixes. doppler carried
62 fragments holding no hand-written code, frozen without the `out=`
contiguity guard and the gh-219 use-after-free fix.

One key ends that. Absent, nothing changes.

GATE: an owned fragment is rendered whole and drift-gated; an apply that
      would delete a unit refuses instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli

FRAG = Path("native") / "src" / "dsp" / "dsp_ext_blk.c"


def _project(tmp_path: Path, *, generated: bool) -> Path:
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
    if generated:
        t = proj / "objects" / "blk.toml"
        t.write_text(
            t.read_text().replace("[blk]", '[blk]\nfragment = "generated"', 1)
        )
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _age(proj: Path) -> str:
    """Revert one wrapper to the bare call an older jm emitted."""
    frag = proj / FRAG
    s = frag.read_text()
    aged = re.sub(
        r"if \(PyArray_SetBaseObject\(([^;]*?)\) < 0\) \{[^}]*\}",
        r"PyArray_SetBaseObject(\1);",
        s,
        count=1,
        flags=re.S,
    )
    assert aged != s, "fixture no longer contains the checked call"
    frag.write_text(aged)
    return aged


class TestSacredIsUnchanged:
    def test_an_absent_key_still_freezes_the_fragment(self, tmp_path):
        """Zero churn is the point: a manifest that never mentions the key
        behaves exactly as it did."""
        proj = _project(tmp_path, generated=False)
        aged = _age(proj)
        assert run_cli("apply", cwd=proj).returncode == 0
        assert (proj / FRAG).read_text() == aged
        assert run_cli("status", "--check", cwd=proj).returncode == 0


class TestGeneratedReceivesFixes:
    def test_apply_renders_it_whole(self, tmp_path):
        proj = _project(tmp_path, generated=True)
        _age(proj)
        assert run_cli("apply", cwd=proj).returncode == 0
        src = (proj / FRAG).read_text()
        assert "PyArray_SetBaseObject" in src and "< 0" in src, src[:400]

    def test_a_drifted_owned_fragment_is_drift(self, tmp_path):
        """The other half. Without this the fix is delivered but nothing
        reports a file that drifts away again."""
        proj = _project(tmp_path, generated=True)
        assert run_cli("status", "--check", cwd=proj).returncode == 0
        _age(proj)
        assert run_cli("status", "--check", cwd=proj).returncode == 1
        assert "STALE" in run_cli("status", cwd=proj).stdout


class TestItRefusesToDeleteHandWrittenCode:
    def test_apply_refuses_and_keeps_the_unit(self, tmp_path):
        """`adopt` refuses such a flip, but the key is a TOML line anyone
        can write. Without this, doing so deletes the unit silently on the
        next apply -- the one outcome the read-only half exists to
        prevent, reachable by editing one line.
        """
        proj = _project(tmp_path, generated=False)
        frag = proj / FRAG
        frag.write_text(
            frag.read_text()
            + "\nstatic PyObject *\nBlkObj_mine(PyObject *s)\n"
            "{\n    return NULL;\n}\n"
        )
        before = frag.read_text()
        t = proj / "objects" / "blk.toml"
        t.write_text(
            t.read_text().replace("[blk]", '[blk]\nfragment = "generated"', 1)
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode != 0, r.stdout
        out = r.stdout + r.stderr
        assert "would delete hand-written code" in out, out
        assert "only here: fn:BlkObj_mine" in out, out
        assert "adopt --check" in out, out
        # ...and nothing was written.
        assert frag.read_text() == before
