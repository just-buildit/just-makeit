"""gh-2263: `migrate-to-fragments` writes ONE `include`.

A manifest that already has `include` and still holds an inline section --
a project half-migrated, or one whose author pasted a fragment back -- was
migrated by carrying the old `include` over with the other root keys AND
prepending a fresh, merged one. The manifest then said `include` twice,
`tomllib` refused it (`Cannot overwrite a value`), and the undo put the
tree back: the migration could not run at all.

The contract held here, for an inline module, an inline object and both:
the migration exits 0, the manifest parses with exactly one `include`, that
list reaches every fragment the run wrote without naming one twice, and
`_config.load` sees the same components and modules as before.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli

from just_makeit import _config as C


def _ok(*args: str, cwd: Path) -> None:
    res = run_cli(*args, cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr


def _inline(root: Path, fragment: str) -> None:
    """Paste a fragment back into the manifest, as the issue's repro does."""
    frag = root / fragment
    manifest = root / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\n"
        + frag.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    frag.unlink()


def _shape(root: Path) -> tuple[list[str], list[str]]:
    cfg = C.load(root)
    return sorted(C.components(cfg)), sorted(C.modules(cfg))


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _ok("new", "p", cwd=tmp_path)
    root = tmp_path / "p"
    _ok("module", "zz", cwd=root)
    _ok("object", "qq", cwd=root)
    return root


def _include_lines(root: Path) -> list[str]:
    text = (root / C.FILENAME).read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.startswith("include")]


@pytest.mark.parametrize(
    "inline",
    [
        ["modules/zz.toml"],
        ["objects/qq.toml"],
        ["modules/zz.toml", "objects/qq.toml"],
    ],
    ids=["module", "object", "both"],
)
def test_migrate_beside_an_include_writes_one(project, inline):
    before = _shape(project)
    include_before = C.load_manifest(project)["include"]
    for frag in inline:
        _inline(project, frag)

    _ok("migrate-to-fragments", cwd=project)

    assert len(_include_lines(project)) == 1, _include_lines(project)
    manifest = C.load_manifest(project)
    # The fragments were reached by the include already there, so the
    # merged list is that one, with no glob named twice.
    assert manifest["include"] == include_before
    assert set(manifest) <= {"project", "include"}
    for frag in inline:
        assert (project / frag).is_file()
    assert _shape(project) == before


@pytest.mark.parametrize(
    "include, inline, expected",
    [
        # `*/*.toml` already reaches both new fragments: nothing is added.
        (
            '["*/*.toml"]',
            ["objects/qq.toml", "modules/zz.toml"],
            ["*/*.toml"],
        ),
        # An explicit path to the (still separate) module fragment does not
        # reach the object's new fragment: its glob is added, once.
        (
            '["modules/zz.toml"]',
            ["objects/qq.toml"],
            ["modules/zz.toml", "objects/*.toml"],
        ),
    ],
    ids=["glob-covers", "path-covers-other"],
)
def test_reach_is_asked_of_resolution_not_spelling(
    project, include, inline, expected
):
    """An entry spelled otherwise that already reaches a fragment is enough.

    Coverage comes from `_config._resolve_includes`, the reading `load`
    makes, so the merged list neither doubles an entry under another
    spelling nor misses a fragment no entry reaches.
    """
    before = _shape(project)
    manifest = project / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    old = 'include = ["objects/*.toml", "modules/*.toml"]'
    assert text.count(old) == 1
    manifest.write_text(
        text.replace(old, f"include = {include}"), encoding="utf-8"
    )
    for frag in inline:
        _inline(project, frag)

    _ok("migrate-to-fragments", cwd=project)

    assert len(_include_lines(project)) == 1, _include_lines(project)
    assert C.load_manifest(project)["include"] == expected
    assert _shape(project) == before
