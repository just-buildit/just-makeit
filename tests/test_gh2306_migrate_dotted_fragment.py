"""gh-2306: `migrate-to-fragments` names a module's fragment as every writer.

`jm module` and the manifest's save route a dotted module's section to
``modules/<cname>.toml`` (``modules/dsp_filters.toml``): one clean
extension that the ``modules/*.toml`` glob still reaches, the dotted id
living inside the file. `migrate-to-fragments` spelled the id itself
(``modules/dsp.filters.toml``), so two projects with one manifest held
differently named fragments depending on how they got there.

Both now ask `_config.module_fragment`. The gate builds the same modules
both ways and requires the same fragment files, then that the migrated
project is clean under `status --check`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

#: A flat id and a dotted one, nested two deep: the dotted ones are where
#: the id and its cname differ.
MODULES = ("filter", "dsp.filters", "dsp.fx.reverb")


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr


def _project(tmp_path: Path, name: str, *, fragments: bool) -> Path:
    _ok("new", name, *([] if fragments else ["--no-fragments"]), cwd=tmp_path)
    root = tmp_path / name
    for i, mod in enumerate(MODULES):
        _ok("module", mod, cwd=root)
        _ok("object", f"o{i}", "--module", mod, cwd=root)
    return root


def _fragments(root: Path) -> set[str]:
    return {p.name for p in (root / "modules").glob("*.toml")}


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    root = _project(tmp_path, "inline", fragments=False)
    assert not (root / "modules").exists()
    _ok("migrate-to-fragments", cwd=root)
    return root


def test_migrating_names_each_fragment_as_jm_module_does(
    tmp_path: Path, migrated: Path
) -> None:
    split = _project(tmp_path, "split", fragments=True)
    assert _fragments(migrated) == _fragments(split)
    for mod in MODULES:
        frag = C.module_fragment(migrated, mod)
        assert frag.is_file(), sorted(_fragments(migrated))
        assert frag.stem == C.module_paths(mod).cname


def test_a_migrated_project_is_clean(migrated: Path) -> None:
    r = run_cli("status", "--check", cwd=migrated)
    assert r.returncode == 0, r.stdout + r.stderr
    # ...and a later mutation writes the migrated fragment, not a second.
    before = _fragments(migrated)
    _ok("object", "late", "--module", "dsp.filters", cwd=migrated)
    assert _fragments(migrated) == before
    assert "late" in C.module_fragment(migrated, "dsp.filters").read_text(
        encoding="utf-8"
    )
