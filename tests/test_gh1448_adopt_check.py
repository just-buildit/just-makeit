"""gh-1448: `adopt --check` — what a flip would do, without doing it.

A standalone object's `<comp>_ext.c` is glue: rendered whole, drift-gated,
fixes delivered. A module object's `<mod>_ext_<obj>.c` is the SAME
generated wrapper code and is treated as the opposite kind. So where a
wrapper lives decides whether it receives fixes.

Measured on doppler at 0.82.2: 98 fragments, 89 differing from a fresh
render, 62 of them holding no hand-written code -- missing the `out=`
contiguity guard and the gh-219 use-after-free fix among others. A 4%
hand-written surface freezing the other 96%.

This file covers the READ-ONLY half. doppler's argument for building it
first decides the shape: "without it the only way to learn what a flip
would do is to do it."

GATE: `adopt --check` writes nothing, and refuses a flip that would lose a
      unit.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli

FRAG = Path("native") / "src" / "dsp" / "dsp_ext_blk.c"


def _project(tmp_path: Path) -> Path:
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


class TestItWritesNothing:
    def test_the_tree_is_byte_identical_afterwards(self, tmp_path):
        """The whole point of a --check mode, so it is asserted over the
        WHOLE tree rather than the fragment: a report that quietly
        reformatted something would still be a mutation."""
        proj = _project(tmp_path)
        before = {
            p.relative_to(proj).as_posix(): p.read_bytes()
            for p in sorted(proj.rglob("*"))
            if p.is_file()
        }
        assert run_cli("adopt", "--check", cwd=proj).returncode == 0
        after = {
            p.relative_to(proj).as_posix(): p.read_bytes()
            for p in sorted(proj.rglob("*"))
            if p.is_file()
        }
        assert before == after

    def test_it_refuses_to_run_without_check(self, tmp_path):
        """A command whose subject is hand-edited files does not mutate
        because you forgot a flag."""
        proj = _project(tmp_path)
        r = run_cli("adopt", cwd=proj)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "--check" in (r.stdout + r.stderr)


class TestTheThreeStates:
    def test_a_fresh_fragment_would_flip(self, tmp_path):
        proj = _project(tmp_path)
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 0, r.stdout
        assert "would flip" in r.stdout, r.stdout

    def test_a_hand_only_unit_refuses(self, tmp_path):
        """Not acknowledgeable. The render does not produce it, so a flip
        deletes it -- that is not a migration."""
        proj = _project(tmp_path)
        frag = proj / FRAG
        frag.write_text(
            frag.read_text()
            + "\nstatic PyObject *\nBlkObj_mine(PyObject *s)\n"
            "{\n    return NULL;\n}\n"
        )
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 1, r.stdout
        assert "REFUSES" in r.stdout, r.stdout
        assert "only here: fn:BlkObj_mine" in r.stdout, r.stdout

    def test_a_changed_body_needs_acknowledgement(self, tmp_path):
        """jm cannot tell a hand-written body from a render predating a
        codegen change -- gh-1447's finding -- so it asks."""
        proj = _project(tmp_path)
        frag = proj / FRAG
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
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 1, r.stdout
        assert "needs acknowledgement" in r.stdout, r.stdout
        assert "differs:   fn:BlkObj_execute" in r.stdout, r.stdout
        # ...and it is NOT reported as a refusal: the two are different
        # decisions and doppler asked for them to stay distinct.
        assert "REFUSES" not in r.stdout, r.stdout

    def test_an_already_generated_fragment_is_not_re_reported(self, tmp_path):
        proj = _project(tmp_path)
        frag_toml = proj / "objects" / "blk.toml"
        s = frag_toml.read_text()
        frag_toml.write_text(
            s.replace("[blk]", '[blk]\nfragment = "generated"', 1)
        )
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 0, r.stdout
        assert "already generated" in r.stdout, r.stdout


class TestAViewGoesWithItsParent:
    def test_a_views_fragment_is_reported_under_the_parent(self, tmp_path):
        """A view has no manifest table, so the key on the PARENT governs
        its fragment and a refusal anywhere refuses the set."""
        proj = _project(tmp_path)
        assert (
            run_cli(
                "view",
                "blk",
                "Peek",
                "--module",
                "dsp",
                "--create-fn",
                "blk_peek_create",
                cwd=proj,
            ).returncode
            == 0
        )
        assert run_cli("apply", cwd=proj).returncode == 0
        view_frag = proj / "native" / "src" / "dsp" / "dsp_ext_peek.c"
        assert view_frag.exists(), sorted(
            p.name for p in (proj / "native" / "src" / "dsp").iterdir()
        )
        view_frag.write_text(
            view_frag.read_text()
            + "\nstatic PyObject *\nPeekObj_mine(PyObject *s)\n"
            "{\n    return NULL;\n}\n"
        )
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 1, r.stdout
        # Reported under `blk`, the object whose key governs it.
        assert "REFUSES              blk" in r.stdout, r.stdout
        assert "dsp_ext_peek.c" in r.stdout, r.stdout


class TestLayoutIsNotADifference:
    """gh-1448 first review: zero of doppler's 87 fragments could flip.

    `_norm_unit` collapsed runs of whitespace, which leaves the whitespace
    that is there. doppler sets `c_style = "clang-format"` with a GNU
    `.clang-format`, so the file on disk says ``foo (a, b)`` where jm's
    unformatted render says ``foo(a, b)`` -- and every unit of every
    fragment read as differing, against 46 that `jm status` calls
    byte-identical to a fresh render.

    The comparison is a TOKEN STREAM now. A formatter rewrites whitespace
    and line breaks and cannot change tokens; string content, the one thing
    it may re-wrap, is masked before the comparison.
    """

    def test_gnu_layout_against_the_knr_render_is_identical(self):
        from just_makeit._docsync import fragment_unit_diff

        knr = (
            "static PyObject *\nf(PyObject *self) {\n"
            "    if (!self->handle) {\n        foo(a, b);\n"
            "        return NULL;\n    }\n    return g(self->handle);\n}\n"
        )
        gnu = (
            "static PyObject *\nf (PyObject *self)\n{\n"
            "  if (!self->handle)\n    {\n      foo (a, b);\n"
            "      return NULL;\n    }\n  return g (self->handle);\n}\n"
        )
        d = fragment_unit_diff(gnu, knr)
        assert d.identical == ("fn:f",), d
        assert d.differing == (), d

    def test_a_real_change_still_differs(self):
        """The narrowing must not swallow its own subject."""
        from just_makeit._docsync import fragment_unit_diff

        a = "static PyObject *\nf(PyObject *s) {\n    return NULL;\n}\n"
        b = "static PyObject *\nf(PyObject *s) {\n    return Py_None;\n}\n"
        assert fragment_unit_diff(a, b).differing == ("fn:f",)

    def test_two_identifiers_do_not_merge(self):
        """Stripping whitespace outright would make `int x` == `intx`."""
        from just_makeit._docsync import _norm_unit

        assert _norm_unit("int x;") != _norm_unit("intx;")

    def test_a_formatted_project_reports_would_flip(self, tmp_path):
        """End to end, with the project's own formatter in play."""
        proj = _project(tmp_path)
        (proj / ".clang-format").write_text(
            "BasedOnStyle: GNU\nColumnLimit: 79\n"
        )
        cfg = proj / "just-makeit.toml"
        cfg.write_text(
            cfg.read_text().replace(
                "[project]", '[project]\nc_style = "clang-format"', 1
            )
        )
        assert run_cli("apply", cwd=proj).returncode == 0
        r = run_cli("adopt", "--check", cwd=proj)
        assert r.returncode == 0, r.stdout
        assert "would flip" in r.stdout, r.stdout


class TestABindingAheadRefuses:
    def test_an_undeclared_optional_arg_is_not_acknowledgeable(self):
        """gh-1448 requirement 2: block, not warn.

        Here the FILE is the superset, so re-rendering does not deliver a
        fix -- it deletes a feature (doppler's `Resampler.execute_ctrl`
        takes an `out=` its manifest never declared).
        """
        from just_makeit import _adopt

        def frag(fmt):
            return (
                "static PyObject *\n"
                "Obj_run(PyObject *self, PyObject *args)\n{\n"
                f'    PyArg_ParseTuple(args, "{fmt}", &a);\n'
                "    return NULL;\n}\n"
                "static PyMethodDef Obj_methods[] = {\n"
                '    {"run", (PyCFunction)Obj_run, METH_VARARGS, NULL},\n'
                "    {NULL}\n};\n"
            )

        assert _adopt.binding_ahead(frag("OO|O"), frag("OO")) == ("run",)
        # ...and the other direction is NOT a refusal: there the manifest
        # is ahead, which is an ordinary undelivered fix.
        assert _adopt.binding_ahead(frag("OO"), frag("OO|O")) == ()
