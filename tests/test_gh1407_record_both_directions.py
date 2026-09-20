"""gh-1407: the record declaration is authoritative in BOTH directions.

gh-1405 introduced `[[<obj>.records]]` as "a record named ONCE, for both
directions", and `C.records`' own docstring states the promise: *"Declared
ONCE and referenced by both directions -- ``arg_type = "iq16_t[]"`` on the
method that writes rows, ``record_dtype = "iq16_t"`` on the one that reads
them."* Only the writing half was implemented.

Measured on `f3dee8a`, the reading half neither consulted the declaration nor
refused a name nothing declares -- `--record-dtype totally_undeclared_t`
was accepted and jm printed an invented `typedef` for it. The columns came
from the method's own `result_fields`, which is the restatement the table
exists to remove.

The fix is non-breaking, and that is a property worth pinning rather than
assuming: `--record-dtype` without `--result-field` was ALREADY refused, so
every pre-gh-1405 site carries a full inline declaration (a name plus its
columns) and keeps working untouched. doppler has six such sites across four
fragments, and they adopt the release this lands in.

What is pinned here:

1. a declared record supplies the reading face's columns, all the way to the
   `offsetof` in the generated C -- not just to the manifest;
2. restating them beside a declared name is REFUSED, not ignored (a dropped
   restatement looks identical to one that agrees, until it does not);
3. the old spelling -- an undeclared name plus inline `result_fields` --
   still works, unchanged;
4. `jm script` reconstructs the declaration, ahead of the methods that
   reference it. It emitted nothing at all before, so a replayed script died
   on the writing face's own refusal.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli


def _project(tmp_path: Path, name: str = "p") -> Path:
    """A bare component to hang records off."""
    root = tmp_path / "w"
    root.mkdir()
    r = run_cli(
        "new",
        name,
        "--object",
        "ring",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root / name


def _declare_iq16(proj: Path) -> None:
    r = run_cli(
        "record",
        "ring",
        "iq16_t",
        "--field",
        "i:int16_t",
        "--field",
        "q:int16_t",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr


def _read_method(proj: Path, name: str, *extra: str):
    return run_cli(
        "method",
        "ring",
        name,
        "--arg-type",
        "void",
        "--return-type",
        "int16_t",
        "--variable-output",
        "--record-dtype",
        "iq16_t",
        *extra,
        cwd=proj,
    )


class TestTheDeclarationSuppliesTheColumns:
    def test_reading_face_needs_no_result_fields(self, tmp_path):
        """The whole point: declared once, referenced by both directions."""
        proj = _project(tmp_path)
        _declare_iq16(proj)
        r = _read_method(proj, "wait")
        assert r.returncode == 0, r.stderr

        frag = (proj / "objects" / "ring.toml").read_text()
        assert 'record_dtype = "iq16_t"' in frag
        # The columns are NOT restated here -- that is the restatement the
        # table removes. Anchored on the key, because the fragment also
        # contains prose about result_fields in its comments.
        assert "result_fields" not in frag

    def test_the_columns_reach_the_generated_c(self, tmp_path):
        """Not just the manifest: the dtype the extension actually builds.

        Verifying the artefact rather than the source. A resolution that
        stopped at the context dict would leave this empty while every
        manifest assertion above still passed.
        """
        proj = _project(tmp_path)
        _declare_iq16(proj)
        assert _read_method(proj, "wait").returncode == 0

        ext = (proj / "native" / "src" / "ring" / "ring_ext.c").read_text()
        assert 'Py_BuildValue("[ss]", "i", "q")' in ext
        assert "offsetof(iq16_t, i)" in ext
        assert "offsetof(iq16_t, q)" in ext

    def test_a_restatement_is_refused_naming_both_sides(self, tmp_path):
        """Ignoring it would be worse: agreement today is not agreement."""
        proj = _project(tmp_path)
        _declare_iq16(proj)
        r = _read_method(proj, "w2", "--result-field", "i:int16_t")
        assert r.returncode == 1
        assert "already declares its columns" in r.stderr
        # Actionable: the refusal names the command that changes them.
        assert "just-makeit record" in r.stderr


class TestTheOldSpellingStillWorks:
    """The non-break guarantee. doppler ships six of these."""

    def test_undeclared_name_with_inline_columns_is_accepted(self, tmp_path):
        proj = _project(tmp_path)
        r = run_cli(
            "method",
            "ring",
            "read",
            "--arg-type",
            "void",
            "--return-type",
            "uint64_t",
            "--variable-output",
            "--record-dtype",
            "dp_tlm_rec_t",
            "--result-field",
            "n:uint64_t",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr
        ext = (proj / "native" / "src" / "ring" / "ring_ext.c").read_text()
        assert "offsetof(dp_tlm_rec_t, n)" in ext

    def test_a_name_with_no_columns_anywhere_is_still_refused(self, tmp_path):
        """The columns must come from SOMEWHERE -- and say from where."""
        proj = _project(tmp_path)
        r = _read_method(proj, "bare")
        assert r.returncode == 1
        assert "--result-field" in r.stderr
        # gh-1407 added the second source to the message.
        assert "just-makeit record ring iq16_t" in r.stderr


class TestScriptRoundTrip:
    def test_script_emits_the_declaration_before_its_referents(self, tmp_path):
        """Order is load-bearing: the writing face refuses an undeclared name.

        `_script` emitted no `record` command at all, so a replayed script
        died at the first `--arg-type 'iq16_t[]'`.
        """
        proj = _project(tmp_path)
        _declare_iq16(proj)
        r = run_cli(
            "method",
            "ring",
            "write",
            "--arg-type",
            "iq16_t[]",
            "--return-type",
            "size_t",
            cwd=proj,
        )
        assert r.returncode == 0, r.stderr
        assert _read_method(proj, "wait").returncode == 0

        out = run_cli("script", cwd=proj)
        assert out.returncode == 0, out.stderr
        script = out.stdout
        assert "just-makeit record ring iq16_t" in script
        assert script.index("just-makeit record ring iq16_t") < script.index(
            "just-makeit method ring write"
        )

    def test_the_emitted_script_replays_to_the_same_manifest(self, tmp_path):
        """The property, not its spelling: replay and compare."""
        proj = _project(tmp_path)
        _declare_iq16(proj)
        assert (
            run_cli(
                "method",
                "ring",
                "write",
                "--arg-type",
                "iq16_t[]",
                "--return-type",
                "size_t",
                cwd=proj,
            ).returncode
            == 0
        )
        assert _read_method(proj, "wait").returncode == 0

        out = run_cli("script", cwd=proj)
        assert out.returncode == 0, out.stderr

        replay = tmp_path / "replay"
        replay.mkdir()
        import subprocess

        done = subprocess.run(
            ["bash", "-s"],
            input=out.stdout,
            cwd=replay,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr + done.stdout
        before = (proj / "objects" / "ring.toml").read_text()
        after = (replay / "p" / "objects" / "ring.toml").read_text()
        assert before == after
