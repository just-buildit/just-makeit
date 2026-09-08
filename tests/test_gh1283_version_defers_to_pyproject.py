"""gh-1283: an omitted `[project] version` defers to `pyproject.toml`.

`just-makeit.toml` carried a second copy of a value that already lives in
`pyproject.toml`, and jm *compared* the two rather than reading one. That made
the manifest a version carrier every release has to remember — the drift
gh-1141 reports, with the duplication that creates it left in place.

Omitting the key was no escape: `project_version` defaulted to ``0.1.0``, so
absence was indistinguishable from declaring ``0.1.0`` and a project that
deleted the duplicate got a permanently red `status --check` reporting drift
against a default it had never written. There was no way to say "the version
lives in `pyproject.toml`, read it from there".

Now absence means **defer**. A manifest that declares a version is untouched,
so this is backward compatible and needs no new vocabulary.

Resolved in `load`, folded back in `save`
-----------------------------------------
`load` is the one place every reader passes through, which is why the
resolution lives there rather than being threaded through the thirteen call
sites of `project_version`. A resolution reaching some callers and not others
is the half-wired shape `_expand_init_groups` documents three functions above:
the PEP 723 app script would render the deferred version while
`native/src/<pkg>_lib.c` rendered ``0.1.0``, and only one of those is checked.

The `save` half is not symmetry for its own sake, and it is the half with
teeth. `_dump`'s ``[project]`` loop emits every key it is handed and has no
``_``-prefix guard of the kind the method-key loop carries, so without the
fold-back the *first* mutating command writes both the marker and the resolved
number into the manifest — silently recreating the carrier the feature exists
to remove, in the one file the author had just cleaned out. `test_save_*`
below is the test that fails when that half is removed.

What this does not claim
------------------------
`bootstrap.toml` still carries its own literal, so it is still a checked copy
and still drifts. Whether it needs one is just-buildit's question, not jm's —
gh-1283 says so explicitly and leaves it open.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _new  # noqa: E402
from just_makeit import _projversion as V  # noqa: E402

#: What `pyproject.toml` is bumped to. Deliberately not a plain ``x.y.z``:
#: a PEP 440 pre-release is a real project's shape and would catch a resolver
#: that reparsed the value instead of carrying the string through.
BUMPED = "1.1.3.dev5"

#: What the scaffold wrote everywhere on day one.
SCAFFOLD = "0.1.0"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real scaffold, built by running the tool.

    Not a hand-written tree: the point of the fixture is the file jm actually
    emits, and a fixture assembled by hand cannot show that `load`/`save`
    round-trip the manifest jm itself wrote. `pyproject.toml` is bumped and
    the manifest's copy deleted, which is the state gh-1283 describes.
    """
    _new.run("vproj", dest=tmp_path, object_names=["gain"])
    root = tmp_path

    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            f'version = "{SCAFFOLD}"', f'version = "{BUMPED}"', 1
        ),
        encoding="utf-8",
    )

    manifest = root / C.FILENAME
    kept = [
        ln
        for ln in manifest.read_text(encoding="utf-8").splitlines(True)
        if not ln.startswith("version = ")
    ]
    manifest.write_text("".join(kept), encoding="utf-8")
    assert "\nversion = " not in manifest.read_text(encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# load: absence resolves
# --------------------------------------------------------------------------


def test_load_reads_the_version_from_pyproject(project: Path) -> None:
    """The headline: omitted means deferred, not ``0.1.0``."""
    assert C.project_version(C.load(project)) == BUMPED


def test_load_marks_the_resolution(project: Path) -> None:
    """`save` can only fold back what it can recognise."""
    assert C.load(project)["project"][C.VERSION_DEFERRED_KEY] is True


def test_a_declared_version_still_wins(project: Path) -> None:
    """Backward compatibility: a manifest that declares one is authoritative,
    and `pyproject.toml` is not consulted. This is the case every existing
    project is in, so it must be byte-identical to before."""
    manifest = project / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "[project]\n", '[project]\nversion = "9.9.9"\n', 1
        ),
        encoding="utf-8",
    )
    cfg = C.load(project)
    assert C.project_version(cfg) == "9.9.9"
    assert C.VERSION_DEFERRED_KEY not in cfg["project"]


def test_no_pyproject_version_falls_back_to_the_old_default(
    project: Path,
) -> None:
    """Nothing to defer to is not an error — it is today's answer."""
    (project / "pyproject.toml").write_text(
        '[project]\nname = "vproj"\n', encoding="utf-8"
    )
    assert C.project_version(C.load(project)) == SCAFFOLD


def test_an_unreadable_pyproject_falls_back(project: Path) -> None:
    """Malformed TOML is the author's problem to see from the build, not a
    traceback out of every jm command that happens to read the manifest."""
    (project / "pyproject.toml").write_text(
        "[project\nname =", encoding="utf-8"
    )
    assert C.project_version(C.load(project)) == SCAFFOLD


def test_a_tool_table_version_is_not_the_projects(project: Path) -> None:
    """Parsed, not matched: ``^version = `` matches under any table."""
    (project / "pyproject.toml").write_text(
        '[tool.poetry]\nversion = "7.7.7"\n[project]\nname = "vproj"\n',
        encoding="utf-8",
    )
    assert C.project_version(C.load(project)) == SCAFFOLD


# --------------------------------------------------------------------------
# save: the resolution never reaches disk
# --------------------------------------------------------------------------


def test_save_does_not_write_the_resolved_version(project: Path) -> None:
    """The fold-back. Without it the carrier is back on the first `save`."""
    C.save(project, C.load(project))
    text = (project / C.FILENAME).read_text(encoding="utf-8")
    assert f'version = "{BUMPED}"' not in text


def test_save_does_not_write_the_marker(project: Path) -> None:
    """`_dump`'s ``[project]`` loop has no ``_``-prefix guard, so the private
    marker is written out verbatim unless it is stripped."""
    C.save(project, C.load(project))
    text = (project / C.FILENAME).read_text(encoding="utf-8")
    assert C.VERSION_DEFERRED_KEY not in text


def test_a_mutating_command_leaves_the_manifest_without_a_version(
    project: Path,
) -> None:
    """The round-trip over the real command surface, not over `save` alone.

    `jm object` loads, mutates and saves; if the resolution survives that, the
    author's cleaned-out manifest silently grows the key back.
    """
    from just_makeit import _object

    _object.run(project, "gain2", None, arg_type="float", return_type="float")

    lines = (project / C.FILENAME).read_text(encoding="utf-8").splitlines()
    assert not [ln for ln in lines if ln.startswith("version = ")]
    assert not [ln for ln in lines if C.VERSION_DEFERRED_KEY in ln]
    # `jm_version` pins the TOOL, not the project, and must stay a literal.
    assert [ln for ln in lines if ln.startswith("jm_version = ")]


def test_a_declared_version_survives_a_mutating_command(
    project: Path,
) -> None:
    """The fold-back must not strip a version the author actually wrote —
    a stripper keyed on the value rather than on the marker would."""
    from just_makeit import _object

    manifest = project / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "[project]\n", '[project]\nversion = "9.9.9"\n', 1
        ),
        encoding="utf-8",
    )
    _object.run(project, "gain3", None, arg_type="float", return_type="float")
    assert 'version = "9.9.9"' in manifest.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the drift class, made unrepresentable
# --------------------------------------------------------------------------


def test_pyproject_can_no_longer_drift_against_the_manifest(
    project: Path,
) -> None:
    """gh-1283's headline ask, and the strongest form of a gh-1141 finding:
    the file jm reads the version FROM cannot disagree with it."""
    reported = {c.rel for c in V.drift(project, C.load(project))}
    assert "pyproject.toml" not in reported


def test_the_other_copies_are_still_checked(project: Path) -> None:
    """A deferral that silenced the whole check would 'fix' gh-1283 by
    removing the gate. The create-only copies still say ``0.1.0`` while the
    project says ``1.1.3.dev5``, and that is real drift gh-1141 must report.

    Named individually rather than counted: a count passes while the set
    quietly changes membership.
    """
    reported = {c.rel for c in V.drift(project, C.load(project))}
    assert "CMakeLists.txt" in reported
    assert "Doxyfile" in reported
    assert "native/src/vproj_lib.c" in reported
    assert all(c.expected == BUMPED for c in V.drift(project, C.load(project)))
