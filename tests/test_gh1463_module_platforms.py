"""gh-1463: ``[module.X] platforms`` scopes a module's extension, on both faces.

doppler's first Windows ``make pyext`` stopped at CMake's GENERATE step: a
``kind = "handle"`` module over a POSIX-only core referenced
``$<TARGET_OBJECTS:stream_core_obj>``, a target that does not exist on
Windows, and jm renders that module's CMake unconditionally. Skipping the
build would not have been enough either -- the owning package's generated
``__init__.py`` imports the module's names unconditionally, so
``import doppler.wfm`` would have failed for the sake of one class.

The fixture is that shape, built by running jm: a plain module ``wfm``, a
restricted plain module ``sink`` sharing its package, and a restricted handle
``hand`` that ``wfm`` re-exports. The two restricted paths are the two ways a
name reaches a shared ``__init__.py`` -- its own import line, and the owning
package's ``reexports``.

GATE: a module that declares ``platforms`` is built, and its names imported,
      only on those platforms -- for every module kind -- and the file jm
      writes for it survives a second apply, ``status --check`` and the
      project's formatter unchanged.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _capsule, _composer, _handle, _keys, _modplatforms
from test_composer_codegen import _cfg as _composer_cfg

_GUARD = 'if(BUILD_PYTHON AND (CMAKE_SYSTEM_NAME STREQUAL "Linux" OR APPLE))'

_HANDLE = """\
[module.hand]
kind = "handle"
backing = "b"
header = "b/b.h"
type_name = "H"
close_fn = "b_close"
create_fn = "b_open"
create_args = []
package = "wfm"
platforms = ["macos", "linux"]
"""


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("platforms")
    _jm("new", "mp", cwd=root)
    proj = root / "mp"
    for args in (
        ("module", "wfm"),
        ("object", "gain", "--module", "wfm"),
        ("module", "sink"),
        ("object", "snk", "--module", "sink"),
    ):
        _jm(*args, cwd=proj)
    mods = proj / "modules"
    with (mods / "sink.toml").open("a") as f:
        f.write('package = "wfm"\nplatforms = ["linux", "macos"]\n')
    (mods / "hand.toml").write_text(_HANDLE)
    with (mods / "wfm.toml").open("a") as f:
        f.write('\n[module.wfm.reexports]\nhand = ["H"]\n')
    _jm("apply", cwd=proj)
    return proj


def _init(project: Path) -> str:
    return (project / "src" / "mp" / "wfm" / "__init__.py").read_text()


# ── CMake: every module kind ─────────────────────────────────────────────────


def _restricted(cfg: dict, mod: str) -> dict:
    cfg["module"][mod]["platforms"] = ["linux", "macos"]
    return cfg


_KIND_RENDERERS = {
    "handle": (
        _handle.render_cmake,
        lambda: {
            "project": {"name": "p"},
            "module": {
                "hand": {
                    "kind": "handle",
                    "backing": "b",
                    "header": "b/b.h",
                    "type_name": "H",
                    "close_fn": "b_close",
                    "create_fn": "b_open",
                    "create_args": [],
                }
            },
        },
        "hand",
    ),
    "capsule": (
        _capsule.render_cmake,
        lambda: {
            "project": {"name": "p"},
            "module": {
                "cap": {"kind": "capsule", "backing": "b", "header": "b/b.h"}
            },
        },
        "cap",
    ),
    "composer": (_composer.render_cmake, _composer_cfg, "wfm_compose"),
}


def test_every_kind_jm_generates_has_a_case():
    # Registration-free over the kinds: a new `kind` in `_keys.KIND_KEYS`
    # fails here until its CMake renderer is shown to honour the key.
    kinds = {
        k.removesuffix(" module")
        for k in _keys.KIND_KEYS
        if k.endswith(" module")
    }
    assert kinds == set(_KIND_RENDERERS)


@pytest.mark.parametrize("kind", sorted(_KIND_RENDERERS))
def test_a_kind_module_is_guarded_only_when_restricted(kind):
    render, cfg, mod = _KIND_RENDERERS[kind]
    plain = render(cfg(), mod)
    assert plain.startswith("if(BUILD_PYTHON)\n"), plain[:80]
    guarded = render(_restricted(cfg(), mod), mod)
    assert guarded.startswith(_GUARD + "\n"), guarded[:80]
    # Nothing else moves: a project that never writes the key sees no churn,
    # and one that does sees exactly one line change.
    assert guarded.replace(_GUARD, "if(BUILD_PYTHON)", 1) == plain


def test_plain_modules_are_guarded_only_when_restricted(project):
    src = project / "native" / "src"
    assert _GUARD in (src / "sink" / "CMakeLists.txt").read_text()
    assert _GUARD in (src / "hand" / "CMakeLists.txt").read_text()
    wfm = (src / "wfm" / "CMakeLists.txt").read_text()
    assert "\nif(BUILD_PYTHON)\n" in wfm and "APPLE" not in wfm


def test_the_full_set_renders_as_absent():
    render, cfg, mod = _KIND_RENDERERS["capsule"]
    full = cfg()
    full["module"][mod]["platforms"] = ["windows", "macos", "linux"]
    assert render(full, mod) == render(cfg(), mod)


# ── Python: the names are absent off-platform, not broken ────────────────────


def _import_wfm(project: Path, tmp_path: Path, platform: str, monkeypatch):
    """Import ``mp.wfm`` as *platform* would, with stand-in submodules.

    The real submodules are extensions this test does not build; a ``.py``
    per leaf defining its names is what the ``__init__.py`` imports either
    way, and only the ``__init__.py`` is under test.
    """
    tree = tmp_path / platform
    shutil.copytree(project / "src" / "mp", tree / "mp")
    wfm = tree / "mp" / "wfm"
    for leaf, name in (("wfm", "Gain"), ("sink", "Snk"), ("hand", "H")):
        (wfm / f"{leaf}.py").write_text(f"class {name}: pass\n")
    monkeypatch.syspath_prepend(str(tree))
    monkeypatch.setattr(sys, "platform", platform)
    for key in [k for k in sys.modules if k == "mp" or k.startswith("mp.")]:
        monkeypatch.delitem(sys.modules, key)
    return importlib.import_module("mp.wfm")


def test_off_platform_the_names_are_absent(project, tmp_path, monkeypatch):
    mod = _import_wfm(project, tmp_path, "win32", monkeypatch)
    assert mod.__all__ == ["Gain"]
    assert not hasattr(mod, "Snk") and not hasattr(mod, "H")


def test_on_platform_every_name_is_there(project, tmp_path, monkeypatch):
    mod = _import_wfm(project, tmp_path, "linux", monkeypatch)
    assert sorted(mod.__all__) == ["Gain", "H", "Snk"]
    assert mod.Snk and mod.H


# ── the file converges ───────────────────────────────────────────────────────


def test_a_second_apply_changes_nothing_and_status_is_clean(project):
    before = _init(project)
    _jm("apply", cwd=project)
    assert _init(project) == before
    _jm("status", "--check", cwd=project)


@pytest.mark.parametrize(
    "variant",
    [
        # What jm writes, and what ruff leaves alone.
        lambda b: b,
        # An older formatter, with no blank line after the nested import.
        lambda b: b.replace(" # noqa: E402\n\n", " # noqa: E402\n"),
        # A formatter that wrapped the `__all__ +=` list.
        lambda b: b.replace(
            '    __all__ += ["Snk"]\n',
            '    __all__ += [\n        "Snk",\n    ]\n',
        ),
    ],
)
def test_a_formatted_block_is_replaced_whole(project, variant):
    """The block regex must take in everything a formatter leaves inside the
    `if`; stopping short at ruff's blank line orphaned `__all__ +=` and the
    next apply wrote a second one."""
    text = variant(_init(project))
    owned = {"sink": ("linux", "macos")}
    again = _modplatforms.guard_init(
        text.replace(
            "__all__ = [", "from .sink import Snk  # noqa: E402\n__all__ = ["
        ),
        owned,
    )
    assert again.count("from .sink import Snk") == 1
    assert again.count('__all__ += ["Snk"]') == 1
    assert '\n        "Snk",\n' not in again


def test_lifting_the_restriction_restores_the_plain_import(tmp_path):
    root = tmp_path
    _jm("new", "lift", cwd=root)
    proj = root / "lift"
    _jm("module", "m", cwd=proj)
    _jm("object", "o", "--module", "m", cwd=proj)
    frag = proj / "modules" / "m.toml"
    base = frag.read_text()
    frag.write_text(base + 'platforms = ["linux"]\n')
    _jm("apply", cwd=proj)
    init = proj / "src" / "lift" / "m" / "__init__.py"
    assert "# jm:platforms m" in init.read_text()
    frag.write_text(base)
    _jm("apply", cwd=proj)
    text = init.read_text()
    assert "jm:platforms" not in text
    assert "\nfrom .m import O  # noqa: E402\n" in text
    assert '__all__ = ["O"]' in text
    _jm("status", "--check", cwd=proj)


# ── the manifest ─────────────────────────────────────────────────────────────


def test_a_misspelt_platform_is_refused(project, tmp_path):
    proj = tmp_path / "bad"
    shutil.copytree(project, proj)
    frag = proj / "modules" / "sink.toml"
    frag.write_text(frag.read_text().replace('"macos"', '"macOS"'))
    r = run_cli("status", cwd=proj)
    assert r.returncode == 1
    assert 'unknown platform "macOS"' in r.stderr


def test_the_key_is_known_on_a_kind_module():
    assert "platforms" in _keys.HANDLE_MODULE_KEYS
    assert "platforms" in _keys.CAPSULE_MODULE_KEYS
    assert "platforms" in _keys.COMPOSER_MODULE_KEYS


def test_the_replayed_script_names_the_key(project):
    out = _jm("script", cwd=project).stdout
    assert '# NOTE: [module.sink] platforms = ["linux", "macos"]' in out
    assert '# NOTE: [module.hand] platforms = ["macos", "linux"]' in out
