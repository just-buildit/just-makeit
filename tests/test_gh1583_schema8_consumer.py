"""gh-1583 part 2: two schema-8 projects built, installed and consumed together.

The consumer smoke (`scripts/consumer-smoke.sh`) carries the issue's two
checks as KNOWN_BROKEN, because it scaffolds with `jm new` and `jm new` stays
on the legacy schema until part 3 ships the `jm upgrade` migration. This test
runs the same two checks on schema-8 projects now, so the layout is proven
before any project is moved into it:

1. **Disjoint installs.** No file alpha installs is one beta installs. In the
   legacy layout both install ``include/clib_common.h``, ``jm_perf.h`` and
   ``jm_simd.h``, and the second install overwrites the first's.
2. **Prefixed includes.** One translation unit includes
   ``"alpha/acore/acore_core.h"`` and ``"beta/bcore/bcore_core.h"`` -- and
   both packages' ``jm_perf.h``, which keep ONE shared guard so their
   fixed-name inline functions are defined once (#1583, #1606) -- with only
   the flags pkg-config hands out.

Each project also configures, builds and passes its own CTest suite, and a
``find_package`` consumer of both links and runs.

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: two schema-8 packages install disjoint files into one prefix, and one
      translation unit includes both by their prefixed paths and builds
      with only what pkg-config and find_package hand out.
"""

# gh-1591: this file's hand-written C and expectations spell jm's bare
# derived names, so its projects opt out of the prefix `jm new` now
# defaults to; the default is gated by tests/test_gh1591_*.py.

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit._new import run as new_run

#: package -> its one component.
PROJECTS = {"alpha": "acore", "beta": "bcore"}


def _run(cmd, cwd, env=None):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, (cmd, r.stdout[-2500:], r.stderr[-2500:])
    return r


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("gh1583s8")
    pfx = root / "prefix"
    for pkg, comp in PROJECTS.items():
        proj = root / pkg
        new_run(
            pkg,
            proj,
            object_names=[comp],
            state_vars=[("level", "float", "1.0")],
            arg_type="float",
            return_type="float",
            perf=True,
            schema=8,
            c_prefix=None,
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
        _run(
            [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-DBUILD_PYTHON=OFF",
                f"-DCMAKE_INSTALL_PREFIX={pfx}",
                f"-DCMAKE_PREFIX_PATH={pfx}",
            ],
            proj,
        )
        _run(["cmake", "--build", "build"], proj)
        _run(["ctest", "--test-dir", "build", "--output-on-failure"], proj)
        _run(["cmake", "--install", "build"], proj)
    return root, pfx


def test_nothing_installs_outside_the_package_directory(world):
    _, pfx = world
    assert sorted(p.name for p in (pfx / "include").iterdir()) == sorted(
        PROJECTS
    )


def test_installs_are_disjoint(world):
    root, _ = world
    seen: "dict[str, str]" = {}
    clash = []
    for pkg in PROJECTS:
        manifest = root / pkg / "build" / "install_manifest.txt"
        for line in manifest.read_text(encoding="utf-8").split():
            if line in seen:
                clash.append(f"{line} ({seen[line]} and {pkg})")
            seen[line] = pkg
    assert clash == []


def _pc_env(pfx: Path) -> "dict[str, str]":
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(pfx / "lib" / "pkgconfig")
    return env


def test_one_unit_includes_both_by_the_prefixed_path(world):
    root, pfx = world
    tu = root / "side_by_side.c"
    tu.write_text(
        '#include "alpha/acore/acore_core.h"\n'
        '#include "beta/bcore/bcore_core.h"\n'
        '#include "alpha/jm_perf.h"\n'
        '#include "beta/jm_perf.h"\n'
        "int main(void) {\n"
        "    acore_state_t *a = acore_create(1.0f);\n"
        "    bcore_state_t *b = bcore_create(1.0f);\n"
        "    float y = acore_step(a, 2.0f) + bcore_step(b, 3.0f);\n"
        "    acore_destroy(a);\n"
        "    bcore_destroy(b);\n"
        "    return y == y ? 0 : 1;\n"
        "}\n",
        encoding="utf-8",
    )
    env = _pc_env(pfx)
    flags = _run(
        ["pkg-config", "--cflags", "--libs", "alpha", "beta"], root, env
    ).stdout.split()
    cc = os.environ.get("CC", "cc")
    exe = root / "side_by_side"
    _run([cc, tu, "-o", exe, *flags, f"-Wl,-rpath,{pfx / 'lib'}"], root, env)
    _run([exe], root)


def test_find_package_consumer_of_both_links_and_runs(world):
    root, pfx = world
    cons = root / "fp"
    cons.mkdir()
    (cons / "c.c").write_text(
        '#include "alpha/alpha.h"\n'
        '#include "beta/beta.h"\n'
        "int main(void) {\n"
        "    acore_destroy(NULL);\n"
        "    bcore_destroy(NULL);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(cons C)\n"
        "find_package(alpha REQUIRED)\n"
        "find_package(beta REQUIRED)\n"
        "add_executable(c c.c)\n"
        "target_link_libraries(c PRIVATE alpha::alpha beta::beta)\n",
        encoding="utf-8",
    )
    _run(["cmake", "-S", ".", "-B", "b", f"-DCMAKE_PREFIX_PATH={pfx}"], cons)
    _run(["cmake", "--build", "b"], cons)
    _run([cons / "b" / "c"], cons)
