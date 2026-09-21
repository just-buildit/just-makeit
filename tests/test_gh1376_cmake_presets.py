"""gh-1376: the generated CMakePresets.json configures what `make build` does.

Visual Studio and VS Code open a folder through `CMakePresets.json`, not
through the Makefile, and neither finds a venv's interpreter on its own:
opening a generated project in Visual Studio 2026 failed at configure with
`Could NOT find Python3 (missing: Python3_EXECUTABLE ... NumPy)`. So `jm new`
now writes a presets file.

That makes TWO configure lines -- the Makefile's `CMAKE_CONFIGURE` and the
presets -- and two configure lines drift. The Makefile stays the source of
truth for HOW the project is configured, so every assertion here reads the
Makefile's answer out of the generated Makefile and requires the preset to
give the same one. None of the expected values is written in this file:

- every `-D<VAR>=` the Makefile passes, every visible configure preset sets
    (so a flag added to the Makefile fails here until the preset carries it);
- `Python3_EXECUTABLE` in particular, because a configure that leaves it
    unset lets CMake pick an interpreter -- the wrong-numpy build gh-814
    refuses. The value must name the project's own `.venv`;
- on Windows, the compiler the Makefile exports and the generator it
    defaults to; elsewhere, no generator, as the Makefile passes no `-G`;
- the preset build tree is ignored by the generated `.gitignore` and is not
    the Makefile's `BUILD_DIR`, so the two never share a CMake cache.

Whether the presets actually BUILD is `test_gh1376_presets_build.py`, which
needs a compiler and so lives on PROJECT_ENV_TESTS.

GATE: the generated CMakePresets.json configures with every setting the
      generated Makefile configures with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _jmrun import run_cli


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("presets")
    r = run_cli("new", "presets_probe", cwd=root)
    assert r.returncode == 0, r.stderr
    return root / "presets_probe"


@pytest.fixture(scope="module")
def presets(project) -> dict:
    return json.loads((project / "CMakePresets.json").read_text())


@pytest.fixture(scope="module")
def makefile(project) -> str:
    return (project / "Makefile").read_text()


def _resolve(presets: dict, name: str) -> dict:
    """A configure preset with its `inherits` chain applied, parent first."""
    by_name = {p["name"]: p for p in presets["configurePresets"]}
    p = by_name[name]
    out: dict = {"cacheVariables": {}}
    parents = p.get("inherits", [])
    for parent in [parents] if isinstance(parents, str) else parents:
        base = _resolve(presets, parent)
        out.update({k: v for k, v in base.items() if k != "cacheVariables"})
        out["cacheVariables"].update(base["cacheVariables"])
    out.update({k: v for k, v in p.items() if k != "cacheVariables"})
    out["cacheVariables"].update(p.get("cacheVariables", {}))
    return out


def _visible(presets: dict) -> list[dict]:
    return [
        _resolve(presets, p["name"])
        for p in presets["configurePresets"]
        if not p.get("hidden")
    ]


def _is_windows(p: dict) -> bool:
    cond = p.get("condition", {})
    return cond.get("type") == "equals" and cond.get("rhs") == "Windows"


def _makefile_defines(makefile: str) -> set[str]:
    """The `-D<VAR>` names in the Makefile's `CMAKE_CONFIGURE` definition."""
    m = re.search(
        r"^CMAKE_CONFIGURE = cmake .*?(?:\n(?![ \t])|\Z)",
        makefile,
        re.MULTILINE | re.DOTALL,
    )
    assert m, "no `CMAKE_CONFIGURE = cmake ...` definition in the Makefile"
    names = set(re.findall(r"-D(\w+)=", m.group(0)))
    assert names, "CMAKE_CONFIGURE passes no -D flags; the parse is broken"
    return names


def _make_var(makefile: str, pattern: str) -> str:
    m = re.search(pattern, makefile, re.MULTILINE)
    assert m, f"Makefile has no line matching {pattern!r}"
    return m.group(1)


def test_every_visible_preset_sets_what_the_makefile_passes(presets, makefile):
    wanted = _makefile_defines(makefile)
    visible = _visible(presets)
    assert visible
    for p in visible:
        missing = wanted - set(p["cacheVariables"])
        assert not missing, f"preset {p['name']!r} does not set {missing}"


def test_python_is_the_projects_venv_never_cmakes_choice(presets):
    for p in _visible(presets):
        exe = p["cacheVariables"]["Python3_EXECUTABLE"]
        assert exe.startswith("${sourceDir}/.venv/"), (p["name"], exe)
        assert "Python3_ROOT_DIR" not in p["cacheVariables"], p["name"]


def test_windows_presets_use_the_makefiles_compiler_and_generator(
    presets, makefile
):
    cc = _make_var(makefile, r"^export CC := (\S+)")
    generator = _make_var(makefile, r"^CMAKE_GENERATOR \?= (\S+)")
    windows = [p for p in _visible(presets) if _is_windows(p)]
    assert windows, "no configure preset is conditioned on Windows"
    for p in windows:
        assert p["cacheVariables"]["CMAKE_C_COMPILER"] == cc, p["name"]
        assert p["generator"] == generator, p["name"]
        # Visual Studio supplies the MSVC environment; the preset must not
        # ask CMake to set up an architecture itself.
        assert p["architecture"]["strategy"] == "external", p["name"]


def test_other_hosts_use_cmakes_default_generator_as_make_does(presets):
    # Off Windows the Makefile passes no -G, so CMake picks its default; a
    # preset that named one would build with a tool the Makefile never needs.
    for p in _visible(presets):
        if not _is_windows(p):
            assert "generator" not in p, p["name"]


def test_every_platform_has_a_preset(presets):
    visible = _visible(presets)
    assert any(_is_windows(p) for p in visible)
    assert any(not _is_windows(p) for p in visible)
    for p in visible:
        assert "condition" in p, f"{p['name']!r} would show on every host"


def test_build_and_test_presets_cover_every_configure_preset(presets):
    names = {p["name"] for p in _visible(presets)}
    for kind in ("buildPresets", "testPresets"):
        referenced = {p["configurePreset"] for p in presets[kind]}
        assert referenced == names, (kind, referenced ^ names)


def test_the_preset_tree_is_ignored_and_is_not_build_dir(
    project, presets, makefile
):
    build_dir = _make_var(makefile, r"^BUILD_DIR\s*\?=\s*(\S+)")
    ignored = set((project / ".gitignore").read_text().splitlines())
    assert "CMakeUserPresets.json" in ignored
    for p in _visible(presets):
        rel = p["binaryDir"].removeprefix("${sourceDir}/")
        assert rel != p["binaryDir"], p["binaryDir"]
        top = rel.split("/")[0]
        assert f"{top}/" in ignored, f"{top}/ is not in .gitignore"
        assert top != build_dir, f"{p['name']!r} shares make's {build_dir}/"


def test_the_make_build_system_gets_no_presets(tmp_path):
    # Only the cmake backend has a CMake configure for a preset to describe.
    r = run_cli("new", "plain", "--build-system", "make", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "plain" / "CMakePresets.json").exists()
