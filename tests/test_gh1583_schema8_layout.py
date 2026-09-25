"""gh-1583 part 2: the header layout is a fact about each PROJECT.

GATE: a project at manifest schema 8 or later keeps every header under
      native/inc/<pkg>/ and spells every include "<pkg>/...", and apply is a
      fixed point there; an older project keeps the legacy layout; and
      render() refuses a template needing the layout whose context lacks it.

A project whose manifest says ``schema >= 8`` keeps its headers under
``native/inc/<pkg>/`` and includes them as ``"<pkg>/..."``; an older one keeps
the legacy layout, byte for byte. The schema decides because jm already
migrates it per project (``jm upgrade``): a global switch would make an
existing project's next ``apply`` write the new layout beside the old one.

``CURRENT_SCHEMA`` is still 7, so ``jm new`` scaffolds legacy projects until
part 3 ships the migration. These tests scaffold schema 8 through
``_new.run(schema=8)`` -- the one seam `apply`'s replay also uses -- and then
drive every other verb through the CLI as an author would.

The build, install and consume half lives in
``test_gh1583_schema8_consumer.py`` (it needs cmake and a compiler).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import _render as R
from just_makeit._new import run as new_run

_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def _scaffold(tmp_path: Path, schema: int, pkg: str = "alpha") -> Path:
    """A project with a standalone object, a module object and perf on."""
    root = tmp_path / pkg
    new_run(
        pkg,
        root,
        object_names=["acore"],
        state_vars=[("level", "float", "1.0")],
        arg_type="float",
        return_type="float",
        perf=True,
        schema=schema,
    )
    r = run_cli("module", "filt", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "object",
        "mfir",
        "--module",
        "filt",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _native_includes(root: Path):
    """(file, target) for every quoted #include in the project's C."""
    for f in sorted((root / "native").rglob("*")):
        if f.suffix in (".c", ".h") and f.is_file():
            for m in _INCLUDE.finditer(f.read_text(encoding="utf-8")):
                yield f, m.group(1)


class TestTheSchemaDecides:
    def test_seven_is_legacy_and_eight_is_prefixed(self):
        seven = {"project": {"name": "p", "schema": "7"}}
        eight = {"project": {"name": "p", "schema": "8"}}
        assert not INC.prefixed(seven)
        assert INC.prefixed(eight)
        assert INC.core_include("g", seven) == "g/g_core.h"
        assert INC.core_include("g", eight) == "p/g/g_core.h"

    def test_a_new_project_is_prefixed(self):
        # Part 3: every project jm scaffolds now is, and `jm upgrade` moves an
        # older one there.
        cur = {"project": {"name": "p", "schema": str(C.CURRENT_SCHEMA)}}
        assert INC.prefixed(cur)

    def test_a_bare_name_is_refused(self):
        # A name does not say which layout its project is in.
        with pytest.raises(TypeError, match="bare name"):
            INC.include("clib_common.h", "p")

    def test_a_tree_with_no_manifest_is_legacy(self, tmp_path):
        assert not INC.prefixed(tmp_path)

    def test_render_refuses_a_layout_slot_the_context_lacks(self):
        # A silent default would write the legacy layout into a schema-8
        # project.
        with pytest.raises(ValueError, match="inc_prefix"):
            R.render('#include "/*<<inc_prefix>>*/x.h"\n', {})


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return _scaffold(tmp_path_factory.mktemp("s8"), 8)


class TestSchema8Scaffold:
    def test_every_header_is_under_the_package(self, root):
        inc = root / INC.INC_DIR
        outside = [
            str(p.relative_to(inc))
            for p in inc.rglob("*")
            if p.is_file() and p.relative_to(inc).parts[0] != "alpha"
        ]
        assert outside == []
        assert (inc / "alpha" / "acore" / "acore_core.h").is_file()
        assert (inc / "alpha" / "mfir" / "mfir_core.h").is_file()
        assert (inc / "alpha" / "clib_common.h").is_file()
        assert (inc / "alpha" / "alpha.h").is_file()

    def test_every_include_resolves_under_the_package(self, root):
        inc = root / INC.INC_DIR
        bad = []
        for f, target in _native_includes(root):
            if (f.parent / target).is_file():
                continue  # beside its includer (jm_test.h, a local .h)
            if (inc / target).is_file() and target.startswith("alpha/"):
                continue
            bad.append(f"{f.relative_to(root)}: {target}")
        assert bad == []

    def test_every_cmake_include_dir_exists(self, root):
        # CMake does not fail on an include directory that is not there, so
        # a per-component `-I native/inc/<comp>` left in the legacy spelling
        # would silently point nowhere. Every one must name a real directory.
        paths = set()
        for cm in root.rglob("CMakeLists.txt"):
            text = cm.read_text(encoding="utf-8")
            paths |= set(
                re.findall(r"\$\{CMAKE_SOURCE_DIR\}/(native/inc[\w/]*)", text)
            )
        assert "native/inc/alpha/acore" in paths, sorted(paths)
        missing = sorted(p for p in paths if not (root / p).is_dir())
        assert missing == []

    def test_jm_shared_headers_keep_one_guard(self, root):
        # They define fixed-name inline functions and macros every _core.h
        # calls: per-package guards would define them twice in a unit that
        # includes two packages (#1583; version skew is #1606).
        hr = INC.header_root(root)
        assert "#ifndef JM_PERF_H" in (hr / "jm_perf.h").read_text()
        assert "#ifndef JM_SIMD_H" in (hr / "jm_simd.h").read_text()

    def test_apply_is_a_fixed_point_in_the_prefixed_layout(self, root):
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_cli("status", "--check", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
        inc = root / INC.INC_DIR
        assert sorted(p.name for p in inc.iterdir()) == ["alpha"]


class TestSchema7Scaffold:
    """The legacy layout: nothing under a package directory."""

    def test_headers_and_includes_are_bare(self, tmp_path):
        root = _scaffold(tmp_path, 7)
        inc = root / INC.INC_DIR
        assert not (inc / "alpha").exists()
        assert (inc / "acore" / "acore_core.h").is_file()
        assert "#ifndef JM_PERF_H" in (inc / "jm_perf.h").read_text()
        assert not any(
            t.startswith("alpha/") for _, t in _native_includes(root)
        )
