"""gh-2095: `_incpath.manifest` answers what `C.load` would, in one process.

The cache behind it was keyed on the central `just-makeit.toml` alone, but
`C.load` merges every fragment its `include` names. So after a command that
wrote only a fragment -- `jm object` and `jm method` on a split layout --
every reader of the cache kept the manifest from before the write: not the
new method, not even the new component. The CLI runs one command per process,
which kept it latent; the test suite drives jm in-process, where it is live,
and `_csym._is_component` (which `backing_stem` relies on) and the doc
reader's methods both read through it.

What changes a manifest input inside one process is jm writing it, so the
cache counts jm's TOML writes (`_textio.toml_writes`) instead of
re-stamping every fragment per lookup. Each test primes the cache, has one input of `C.load`
change, and asks again:

- written by jm: a command (`jm method`), `C.save` through
  `_textio.write_text`, `C.save` REMOVING an emptied fragment, `apply`
  COPYING a fragment in, and `pyproject.toml` (an omitted version is read
  from it);
- pending inside `C.deferred_save`, which `C.load` serves instead of disk;
- by hand: the manifest itself (its own stamp stays in the key), and a
  fragment deleted between two commands (gh-2072's case), which `run_cli`
  reads fresh as a child process would.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _csym as CSYM
from just_makeit import _incpath as INC
from just_makeit import _textio


@pytest.fixture
def root(tmp_path: Path) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    r = run_cli("object", "o", cwd=root)
    assert r.returncode == 0, r.stderr
    # A split layout: the component lives in a fragment, which is the whole
    # premise -- a write to it does not touch the central manifest.
    assert (root / "objects" / "o.toml").is_file()
    return root


def _methods(cfg: dict, obj: str) -> list[str]:
    return [m["name"] for m in cfg.get(obj, {}).get("methods", [])]


def test_a_fragment_write_reaches_the_cache(root: Path):
    """The issue's trigger: `jm method` on a split layout, then the cache."""
    central = (root / "just-makeit.toml").read_bytes()
    assert "o" in INC.manifest(root)
    r = run_cli("method", "o", "m", "--doc", "M.", cwd=root)
    assert r.returncode == 0, r.stderr
    assert (root / "just-makeit.toml").read_bytes() == central
    assert _methods(INC.manifest(root), "o") == ["m"]
    assert _methods(INC.manifest(root), "o") == _methods(C.load(root), "o")


def test_a_save_reaches_the_cache_without_a_command_between(root: Path):
    """The same write with no command boundary in between, so nothing but
    the count of jm's writes can tell the cache the fragment moved."""
    assert _methods(INC.manifest(root), "o") == []
    cfg = C.load(root)
    cfg["o"]["methods"] = [{"name": "m", "arg_type": "void"}]
    C.save(root, cfg)
    assert _methods(INC.manifest(root), "o") == ["m"]


def test_a_fragment_removed_by_save_leaves_the_cache(root: Path):
    """`C.save` removes an emptied fragment rather than writing it, and on a
    split layout that unlink is the ONLY write an undeclare makes."""
    assert run_cli("object", "o2", cwd=root).returncode == 0
    assert CSYM._is_component(root, "o2")
    cfg = C.load(root)
    del cfg["o2"]
    before = _textio.toml_writes()
    C.save(root, cfg)
    assert not (root / "objects" / "o2.toml").exists()
    assert _textio.toml_writes() == before + 1, "the unlink was the only write"
    assert not CSYM._is_component(root, "o2")


def test_a_fragment_copied_in_by_apply_reaches_the_cache(root: Path):
    """`jm apply <fragment>` copies the file into `objects/`."""
    from just_makeit._apply import _compose_fragment

    incoming = root.parent / "o3.toml"
    incoming.write_text(
        (root / "objects" / "o.toml")
        .read_text(encoding="utf-8")
        .replace("[o]", "[o3]")
        .replace("[o.", "[o3."),
        encoding="utf-8",
    )
    assert "o3" not in INC.manifest(root)
    _compose_fragment(root, incoming)
    assert "o3" in C.load(root)
    assert "o3" in INC.manifest(root)


def test_a_fragment_deleted_between_commands_leaves_the_cache(root: Path):
    """gh-2072's reader: an undeclare done by deleting the fragment by hand,
    then a command -- a child would read the tree fresh, and so must this."""
    assert run_cli("object", "o2", cwd=root).returncode == 0
    assert CSYM._is_component(root, "o2")
    (root / "objects" / "o2.toml").unlink()
    assert run_cli("--version", cwd=root).returncode == 0
    assert not CSYM._is_component(root, "o2")


def test_a_hand_edit_of_the_manifest_is_seen(root: Path):
    """The manifest's own stamp stays in the key, as it was before."""
    manifest = root / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    old = 'include = ["objects/*.toml", "modules/*.toml"]'
    assert old in text, text
    assert "o" in INC.manifest(root)
    manifest.write_text(
        text.replace(old, 'include = ["modules/*.toml"]'), encoding="utf-8"
    )
    assert "o" not in INC.manifest(root)


def test_a_deferred_version_follows_pyproject(root: Path):
    """An omitted `[project] version` is read from `pyproject.toml`
    (gh-1283), which makes jm's write of that file an input too."""
    manifest = root / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    assert '\nversion = "0.1.0"\n' in text, text
    manifest.write_text(
        text.replace('\nversion = "0.1.0"\n', "\n"), encoding="utf-8"
    )
    pyproject = root / "pyproject.toml"
    py = pyproject.read_text(encoding="utf-8")
    assert '\nversion = "0.1.0"\n' in py, py
    assert C.project_version(INC.manifest(root)) == "0.1.0"
    _textio.write_text(
        pyproject,
        py.replace('\nversion = "0.1.0"\n', '\nversion = "0.1.10"\n'),
    )
    assert C.project_version(C.load(root)) == "0.1.10"
    assert C.project_version(INC.manifest(root)) == "0.1.10"


def test_a_pending_write_reaches_the_cache(root: Path):
    """Inside a deferral `C.load` serves the pending write, so the cache,
    keyed on the disk, must not answer for it."""
    assert "zz" not in INC.manifest(root)
    with C.deferred_save():
        cfg = C.load(root)
        cfg["zz"] = dict(cfg["o"])
        C.save(root, cfg)
        assert "zz" in C.load(root)
        assert "zz" in INC.manifest(root)
