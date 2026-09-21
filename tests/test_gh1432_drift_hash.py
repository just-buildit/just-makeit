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
        assert "declares strict-input" in out, out

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
