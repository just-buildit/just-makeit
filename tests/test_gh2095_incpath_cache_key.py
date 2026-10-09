"""gh-2095: `_incpath.manifest` answers what `C.load` would, in one process.

The cache behind it was keyed on the central `just-makeit.toml` alone, but
`C.load` merges every fragment its `include` names. So after a command that
wrote only a fragment -- `jm object` and `jm method` on a split layout --
every reader of the cache kept the manifest from before the write: not the
new method, not even the new component. The CLI runs one command per process,
which kept it latent; the test suite drives jm in-process, where it is live,
and `_csym._is_component` (which `backing_stem` relies on) and the doc
reader's methods both read through it.

Each test primes the cache, changes one input of `C.load`, and asks again:
a fragment written by a command, a fragment deleted by hand, and a write
pending inside `C.deferred_save`, which `C.load` serves instead of the disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _csym as CSYM
from just_makeit import _incpath as INC


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


def test_a_fragment_deleted_leaves_the_cache(root: Path):
    """gh-2072's reader: an undeclare done by deleting the fragment."""
    assert run_cli("object", "o2", cwd=root).returncode == 0
    assert CSYM._is_component(root, "o2")
    (root / "objects" / "o2.toml").unlink()
    assert "o2" not in C.load(root)
    assert not CSYM._is_component(root, "o2")


def test_an_include_dropped_with_its_file_reloads(root: Path):
    """The cached `include` patterns are the OLD manifest's: resolving an
    explicit one whose file is gone would raise where `C.load` succeeds, so
    a changed manifest reloads before they are read."""
    manifest = root / "just-makeit.toml"
    before = manifest.read_text(encoding="utf-8")
    old = 'include = ["objects/*.toml"'
    assert old in before, before
    (root / "extra.toml").write_text("", encoding="utf-8")
    manifest.write_text(
        before.replace(old, 'include = ["extra.toml", "objects/*.toml"'),
        encoding="utf-8",
    )
    assert "o" in INC.manifest(root)
    manifest.write_text(before + "\n", encoding="utf-8")
    (root / "extra.toml").unlink()
    assert "o" in INC.manifest(root)


def test_a_deferred_version_follows_pyproject(root: Path):
    """An omitted `[project] version` is read from `pyproject.toml`
    (gh-1283), which makes that file an input of `C.load` too."""
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
    pyproject.write_text(
        py.replace('\nversion = "0.1.0"\n', '\nversion = "0.1.10"\n'),
        encoding="utf-8",
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
