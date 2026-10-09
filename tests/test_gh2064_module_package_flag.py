"""gh-2064: `jm module --package` declares `[module.X] package` up front.

``[module.X] package`` (gh-523) had no CLI flag. The documented route was
`jm module mod`, then adding ``package = "other"`` to the manifest. By then
`jm module` had already written ``src/<pkg>/mod/__init__.py`` and
``mod.pyi`` for the module id. `apply` wrote the module into ``other/`` and
left those two files behind, owned by nothing, and `status` did not report
them. `jm script` could not replay the key either: it printed a NOTE asking
the reader to add it back by hand, which is the same route.

`jm module <id> --package <dir>` now writes the key before anything else is
written, and `jm script` replays it as that flag. The key set by hand on a
module that already exists still leaves the old directory behind; that is
gh-2081, ratcheted in `test_gh2057_verb_leaves_what_apply_writes.py`.

The value is a directory below ``src/<pkg>/``, and both routes to it
refuse one that is not, before anything is written: the flag in
`_module.run`, the manifest key when the manifest loads, by one rule
(`_config.validate_module_package`). Accepted as written, ``../evil`` made
``src/evil/``, outside the package, and ``a b``, ``1x`` and ``Other-Pkg``
made directories Python cannot import.

GATE: `jm module mod --package other` then `jm object o --module mod`
      writes nothing under ``src/<pkg>/mod/``, `status --check` exits 0, and
      `jm script` replays the key; and a value that is not a directory
      below ``src/<pkg>/`` is refused on the flag and in the manifest, with
      the tree byte-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli, script_round_trip
from just_makeit import _config as C

#: Not a directory below src/<pkg>/: the first escapes it, the rest are
#: directories Python cannot import.
NOT_A_PACKAGE_DIR = ("../evil", "a b", "1x", "Other-Pkg")

_RULE = "is not a directory below src/<pkg>/"


def _new(tmp_path: Path, *steps: tuple) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for argv in steps:
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, (argv, r.stderr)
    return root


def _tree(base: Path) -> "dict[str, bytes]":
    """Every file under *base*, which holds the project and its parent: a
    value that escapes ``src/<pkg>/`` writes there."""
    return {
        p.relative_to(base).as_posix(): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_the_flag_declares_the_package_before_anything_is_written(tmp_path):
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for argv in (
        ("module", "mod", "--package", "other"),
        ("object", "o", "--module", "mod"),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, (argv, r.stderr)

    assert C.module_package(C.load(root), "mod") == "other"
    assert (root / "src" / "p" / "other" / "mod.pyi").is_file()
    assert not (root / "src" / "p" / "mod").exists(), sorted(
        p.relative_to(root).as_posix()
        for p in (root / "src" / "p" / "mod").rglob("*")
    )
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout

    script = run_cli("script", cwd=root).stdout
    assert "--package other" in script
    # The hand-edit NOTE it replaces would send the reader down gh-2081.
    assert "# NOTE: [module.mod] package" not in script
    orig, replay = script_round_trip(root, tmp_path / "replay")
    assert orig == replay


@pytest.mark.parametrize("value", ("dsp/io", "."))
def test_a_nested_package_and_the_package_itself_are_accepted(tmp_path, value):
    root = _new(tmp_path, ("module", "mx", "--package", value))
    pyi = root / "src" / "p" / value / "mx.pyi"
    assert pyi.is_file()
    assert run_cli("status", "--check", cwd=root).returncode == 0


@pytest.mark.parametrize("value", NOT_A_PACKAGE_DIR)
def test_the_flag_refuses_a_value_that_is_not_a_package_dir(tmp_path, value):
    root = _new(tmp_path)
    before = _tree(tmp_path)
    r = run_cli("module", "mx", "--package", value, cwd=root)
    assert r.returncode == 1
    assert r.stderr.startswith(f'error: --package "{value}" {_RULE}'), r.stderr
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("value", NOT_A_PACKAGE_DIR)
def test_the_manifest_key_is_refused_when_the_manifest_loads(tmp_path, value):
    root = _new(tmp_path, ("module", "mx"))
    cfg = C.load(root)
    cfg["module"]["mx"]["package"] = value
    C.save(root, cfg)
    before = _tree(tmp_path)
    for verb in ("status", "apply"):
        r = run_cli(verb, cwd=root)
        assert r.returncode == 1, verb
        assert f'[module.mx] package = "{value}" {_RULE}' in r.stderr, (
            verb,
            r.stderr,
        )
        assert _tree(tmp_path) == before, verb
