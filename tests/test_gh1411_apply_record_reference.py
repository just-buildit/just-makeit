"""gh-1411: `jm apply` must accept a record reference `jm method` wrote.

Shipped in v0.79.0 and measured against the released wheel, not the tree:
``jm method ring write --arg-type 'iq16_t[]'`` returned 0, and ``jm apply``
on the manifest that command had just written returned 1 with *"unknown
arg_type 'iq16_t[]'"*. gh-1405's headline feature was unusable through
``apply``, which is the manifest-first path its adopter (doppler#1358) uses.

Two faults, one symptom, and the tests below sabotage them separately
because either alone still breaks the feature:

1. **The manifest gate had no record awareness.**
   ``_config.manifest_type_errors`` refused anything outside the scalar
   allowlist. gh-1405 gave the CLI front-end its escape hatch
   (`_cli_method.py`) and never gave this one — the same question, one face
   wired.
2. **`apply` never replayed the declarations.** It rebuilds each component
   in a temp tree, and a member resolves its record by reading the manifest
   it is being replayed INTO. With no records there, the renderer died on
   ``_CTYPE_META['iq16_t']``. Records are now written to the temp manifest
   before the members that reference them — the ordering rule `jm script`
   needed in gh-1407, one path over.

Why the suite did not catch it: every gh-1405 test drives the CLI, and
gh-1407's round trip replays an emitted *script*, which is the CLI again.
Nothing scaffolded a record and then called `apply`. So these tests assert
on the APPLY path, and the last one pins the two faces to each other so a
future fix to one cannot drift from the other.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

PYI = Path("src") / "p" / "ring.pyi"
EXT = Path("native") / "src" / "ring" / "ring_ext.c"


def _project_with_record(tmp_path: Path) -> Path:
    """A component with a declared record and a member taking rows of it."""
    root = tmp_path / "w"
    root.mkdir()
    r = run_cli(
        "new",
        "p",
        "--object",
        "ring",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    proj = root / "p"
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
    return proj


class TestApplyAcceptsWhatMethodWrote:
    def test_apply_succeeds_on_the_manifest_method_just_wrote(self, tmp_path):
        """The shipped bug, in one assertion."""
        proj = _project_with_record(tmp_path)
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "unknown arg_type" not in r.stderr

    def test_status_check_passes_too(self, tmp_path):
        """`status --check` sits behind the same gate, so it broke with it."""
        proj = _project_with_record(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        r = run_cli("status", "--check", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr


class TestTheManifestFirstPath:
    def test_glue_regenerates_from_the_manifest_alone(self, tmp_path):
        """Delete the generated glue and rebuild it — doppler's own path.

        This is the assertion that fails on fault 2 alone: the gate can be
        record-aware and `apply` still cannot BUILD the thing, because the
        temp manifest it replays into has no declaration to resolve.
        """
        proj = _project_with_record(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        (proj / EXT).unlink()
        (proj / PYI).unlink()

        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
        ext = (proj / EXT).read_text()
        # The dtype is built from the compiler's own layout, so these are
        # what prove the DECLARATION reached the generator -- not merely
        # that a file was written.
        assert "offsetof(iq16_t, i)" in ext
        assert "offsetof(iq16_t, q)" in ext

    def test_an_undeclared_name_is_still_refused(self, tmp_path):
        """The gate still gates. Widening it to accept anything would pass
        every test above and is exactly the wrong fix."""
        proj = _project_with_record(tmp_path)
        frag = proj / "objects" / "ring.toml"
        frag.write_text(
            frag.read_text().replace(
                'arg_type = "iq16_t[]"', 'arg_type = "nope_t[]"'
            )
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 1
        assert "nope_t[]" in r.stderr


class TestTheTwoFacesAgree:
    def test_cli_and_apply_generate_the_same_stub(self, tmp_path):
        """Pinned to each other, because this bug WAS the two disagreeing.

        Not an assertion about what the annotation should be -- that is
        gh-1405's business -- only that one path cannot drift from the other
        again without a test naming it.
        """
        proj = _project_with_record(tmp_path)
        assert run_cli("apply", cwd=proj).returncode == 0
        from_cli = (proj / PYI).read_text()

        (proj / PYI).unlink()
        assert run_cli("apply", cwd=proj).returncode == 0
        from_apply = (proj / PYI).read_text()

        assert from_cli == from_apply
