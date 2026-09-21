"""gh-1432: a declared feature edited after first render went unreported.

A sacred `*_ext_<obj>.c` fragment only ever GAINS members on apply, so a
changed member stays as written while the `.pyi` moves. `_docsync` reports
that -- on three axes: the METH flags, the PyArg format string, and the
return shape.

`status_errors`, `strict` and `releases` move **none** of them. Same calling
convention, same format, same thing comes back -- and a different body. So a
manifest edit after first render was silently absent from the binding:

    $ just-makeit apply
    Done!  Project already matches just-makeit.toml — nothing to do.
    $ grep -c 'case C:' native/src/m/m_ext_r.c
    0
    $ just-makeit status --check ; echo $?
    0

Found by doppler sabotaging its own suite to prove the tests could fail:
removing a `status_errors` row and re-applying left everything green.

The fourth axis is the same shape as the third -- presence of a construct,
compared in the same direction (what the reference declares and the fragment
lacks), so a hand-written wrapper implementing the feature carries the marker
and never reads as drift.

**A message edited in the manifest is deliberately NOT reported.** Strings
are masked, because an author may legitimately word a hand-written raise
differently and a false positive would be on correct code. Adding or
removing a message SLOT does move the raise axis, since it swaps
`PyErr_SetString` for `PyErr_Format`.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

FRAG = Path("native") / "src" / "m" / "m_ext_r.c"


def _project(tmp_path: Path) -> Path:
    """A MODULE object -- the shape that has a per-object sacred fragment."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    assert (
        run_cli(
            "object",
            "r",
            "--module",
            "m",
            "--no-state",
            "--no-step",
            "--init-param",
            "n:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    return proj


def _borrow_with_table(proj: Path) -> None:
    r = run_cli(
        "method",
        "r",
        "wait",
        "--module",
        "m",
        "--borrow",
        "--param",
        "n:size_t",
        "--return-type",
        "float _Complex",
        "--status-fn",
        "r_status",
        "--status-error",
        "A:ValueError:bad",
        "--status-error",
        "B:EOFError:end",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr


def _edit(proj: Path, old: str, new: str) -> None:
    frag = proj / "objects" / "r.toml"
    s = frag.read_text()
    assert old in s, old
    frag.write_text(s.replace(old, new, 1))


class TestAnEditedFeatureIsReported:
    def test_an_added_status_row(self, tmp_path):
        proj = _project(tmp_path)
        _borrow_with_table(proj)
        _edit(
            proj,
            '[[r.methods.status_errors]]\nstatus = "B"',
            '[[r.methods.status_errors]]\nstatus = "C"\n'
            'error = "RuntimeError"\nmessage = "third"\n\n'
            '[[r.methods.status_errors]]\nstatus = "B"',
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0
        out = r.stdout + r.stderr
        assert "binding no longer matches the manifest" in out, out
        # ...and it NAMES the row, rather than saying something changed.
        assert "declares C" in out, out
        # The binding really is without it -- the report is not a false
        # alarm about a file that already has the row.
        assert "case C:" not in (proj / FRAG).read_text()

    def test_strict_added_to_an_existing_method(self, tmp_path):
        proj = _project(tmp_path)
        assert (
            run_cli(
                "method",
                "r",
                "write",
                "--module",
                "m",
                "--arg-type",
                "float _Complex[]",
                "--return-type",
                "bool",
                cwd=proj,
            ).returncode
            == 0
        )
        _edit(proj, 'name = "write"', 'name = "write"\nstrict = true')

        r = run_cli("apply", cwd=proj)
        out = r.stdout + r.stderr
        # The sentence, not the label: "declares strict-input" named a
        # key no manifest has. This one says what the fragment DOES.
        assert "declares strict" in out, out
        assert "refused rather than converted" in out, out

    def test_status_check_is_not_the_reporter(self, tmp_path):
        """`status` surfaces it; the drift itself is advisory.

        No jm command clears a sacred fragment, so this stays a report
        rather than a gate -- the remaining difference is one only the
        author can settle, which is what `warn_signature_drift` says.
        """
        proj = _project(tmp_path)
        _borrow_with_table(proj)
        _edit(
            proj,
            '[[r.methods.status_errors]]\nstatus = "B"',
            '[[r.methods.status_errors]]\nstatus = "C"\n'
            'error = "RuntimeError"\nmessage = "third"\n\n'
            '[[r.methods.status_errors]]\nstatus = "B"',
        )
        r = run_cli("status", cwd=proj)
        out = r.stdout + r.stderr
        assert "no longer matches" in out, out
        # The row BY NAME. Without it this passed under every sabotage of
        # the new axis -- a pre-existing path reports *something* for this
        # edit, so asserting only the headline tested nothing of the fix.
        assert "declares C" in out, out


class TestItDoesNotCryWolf:
    def test_a_freshly_rendered_fragment_is_silent(self, tmp_path):
        """The overwhelmingly common case, and the one a false positive
        would train everyone to ignore."""
        proj = _project(tmp_path)
        _borrow_with_table(proj)

        r = run_cli("apply", cwd=proj)
        assert "no longer matches" not in (r.stdout + r.stderr)

    def test_a_removed_row_is_the_authors_business(self, tmp_path):
        """The direction is the classification.

        A fragment carrying MORE than the manifest declares is the author's
        body doing more than jm would, which is the entire point of a
        sacred fragment -- so it is not reported, exactly as the two older
        axes decided.
        """
        proj = _project(tmp_path)
        _borrow_with_table(proj)
        _edit(
            proj,
            '\n[[r.methods.status_errors]]\nstatus = "B"\nerror = "EOFError"',
            "",
        )
        r = run_cli("apply", cwd=proj)
        assert "no longer matches" not in (r.stdout + r.stderr)


def _out_method(proj: Path, name: str, *extra: str) -> None:
    """A `variable_output` method -- the shape that offers `out=`."""
    r = run_cli(
        "method",
        "r",
        name,
        "--module",
        "m",
        "--arg-type",
        "float _Complex[]",
        "--return-type",
        "float _Complex",
        "--variable-output",
        *extra,
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr


class TestTheOutGuardIsNotStrict:
    """gh-1432 / doppler#1440: one macro, two guards, one label.

    `PyArray_IS_C_CONTIGUOUS` was the whole of the `strict-input` marker,
    described as "the spelling no coercing wrapper has". jm's own `out=`
    buffer guard has it -- so EVERY `variable_output` method offering
    `out=` carried the marker, and 15 doppler fragments whose manifests
    contain no `strict` anywhere were reported as declaring it.

    Only the operand tells the two apart: the `out=` guard tests
    `out_obj`, the strict refusal tests the input's own `<param>_obj`.
    """

    def test_a_method_with_out_does_not_read_as_strict(self, tmp_path):
        proj = _project(tmp_path)
        _out_method(proj, "run")
        assert run_cli("apply", cwd=proj).returncode == 0

        frag = proj / FRAG
        src = frag.read_text()
        assert "out_obj" in src, "fixture no longer renders an out= guard"
        # An OLD fragment: rendered before gh-581's contiguity half. That
        # is doppler's 15 fragments, reproduced rather than described.
        stripped = "\n".join(
            ln
            for ln in src.splitlines()
            if "PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj)" not in ln
        )
        assert stripped != src
        frag.write_text(stripped + "\n")

        out = (lambda r: r.stdout + r.stderr)(run_cli("apply", cwd=proj))
        # The drift is REAL and must still be reported...
        assert "no longer matches" in out, out
        # ...but never as a manifest key this project does not have.
        assert "strict" not in out, out
        assert "out= guard" in out, out
        assert "silently ignored" in out, out

    def test_strict_is_still_seen_when_it_is_really_declared(self, tmp_path):
        """The other direction: narrowing a marker must not lose its
        real subject. A strict method's contiguity test is on the INPUT,
        so removing the `out=` spelling leaves it standing.
        """
        from just_makeit import _docsync

        proj = _project(tmp_path)
        _out_method(proj, "loose")
        _out_method(proj, "tight", "--strict")
        assert run_cli("apply", cwd=proj).returncode == 0

        feat = _docsync._method_feature_symbols((proj / FRAG).read_text())
        assert "strict-input" in feat["tight"], feat["tight"]
        assert "strict-input" not in feat["loose"], feat["loose"]
        # Both offer `out=`, so both carry the guard marker.
        assert "out-contiguity" in feat["tight"]
        assert "out-contiguity" in feat["loose"]

    def test_the_anchor_survives_a_rewrap(self, tmp_path):
        """The operand may be wrapped away from the macro.

        The fragment is clang-formatted in the PROJECT's style, so a
        narrow column can split the guard across lines. A literal anchor
        that stopped matching would hand the `out=` guard's contiguity
        test back to `strict-input` -- silently restoring the bug.
        """
        from just_makeit import _docsync

        proj = _project(tmp_path)
        _out_method(proj, "run")
        assert run_cli("apply", cwd=proj).returncode == 0

        frag = proj / FRAG
        src = frag.read_text()
        wrapped = src.replace(
            "PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj)",
            "PyArray_IS_C_CONTIGUOUS(\n                (PyArrayObject *)\n"
            "                    out_obj)",
        )
        assert wrapped != src
        feat = _docsync._method_feature_symbols(wrapped)
        assert "out-contiguity" in feat["run"], feat["run"]
        assert "strict-input" not in feat["run"], feat["run"]


class TestTheGuardIsSeenInAnyHouseStyle:
    """gh-1448 review: 76 warnings on doppler, 74 of them false.

    The `out=` guard's marker allowed whitespace everywhere except
    between the macro NAME and its opening paren -- which is the one
    place GNU style always puts one. Every fragment in a project with
    `c_style = "clang-format"` then read as MISSING a guard it visibly
    contains, and `apply` told doppler to delete 74 correct files.

    The rewrap test that was meant to cover this wrapped AFTER the open
    paren: the shape I imagined, not the shape a formatter produces.
    """

    SPELLINGS = {
        "knr": "PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj)",
        "gnu": "PyArray_IS_C_CONTIGUOUS ((PyArrayObject *)out_obj)",
        "gnu_wrapped": (
            "PyArray_IS_C_CONTIGUOUS (\n          (PyArrayObject *)\n"
            "            out_obj)"
        ),
        "inner_spaces": (
            "PyArray_IS_C_CONTIGUOUS ( ( PyArrayObject * ) out_obj )"
        ),
    }

    def test_every_house_style_spelling_is_recognised(self):
        from just_makeit._docsync import _OUT_CONTIG_RE

        for name, spelling in self.SPELLINGS.items():
            assert _OUT_CONTIG_RE.search(spelling), name

    def test_a_fragment_written_gnu_style_is_not_reported_missing(self):
        """End to end on the marker: the same body in two house styles
        must carry the same marker set."""
        from just_makeit import _docsync

        def frag(call):
            return (
                "static PyObject *\n"
                "Obj_run (PyObject *self, PyObject *args)\n{\n"
                f"    if (!{call})\n        return NULL;\n"
                "    return NULL;\n}\n"
                "static PyMethodDef Obj_methods[] = {\n"
                '    {"run", (PyCFunction)Obj_run, METH_VARARGS, NULL},\n'
                "    {NULL}\n};\n"
            )

        knr = _docsync._method_feature_symbols(frag(self.SPELLINGS["knr"]))
        gnu = _docsync._method_feature_symbols(frag(self.SPELLINGS["gnu"]))
        assert "out-contiguity" in knr["run"], knr
        assert gnu["run"] == knr["run"], (gnu, knr)

    def test_the_gnu_form_is_not_reported_as_drift_against_the_knr_one(self):
        """The actual damage: `apply` said 74 correct fragments were
        missing the guard, and told the author to delete them."""
        from just_makeit import _docsync

        def frag(call):
            return (
                "static PyObject *\n"
                "Obj_run (PyObject *self, PyObject *args)\n{\n"
                f"    if (!{call})\n        return NULL;\n"
                "    return NULL;\n}\n"
                "static PyMethodDef Obj_methods[] = {\n"
                '    {"run", (PyCFunction)Obj_run, METH_VARARGS, NULL},\n'
                "    {NULL}\n};\n"
            )

        details = _docsync.signature_drift_details(
            frag(self.SPELLINGS["gnu"]), frag(self.SPELLINGS["knr"])
        )
        assert details == {}, details
