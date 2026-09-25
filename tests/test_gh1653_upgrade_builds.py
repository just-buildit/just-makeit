"""gh-1653, compiled: an upgraded tree whose author C lived in the manifest
and in a `JM_DEFINE_STEPS` call BUILDS, tests, imports and exports only
prefixed symbols.

The source half (tests/test_gh1653_upgrade_manifest_c.py) reads what the
upgrade wrote. This reads what the compiler and linker accept: before
gh-1653 the same tree failed to compile on `lo_state_t` in the mixer's
state struct (a manifest `type`), on `lo_create` in its `create()` (a
manifest `create_impl`), and on `lo_steps` / `lo_step_batch` the macro
pasted from its unmoved `lo` argument.

GATE: set `c_prefix` -> `jm upgrade` -> `jm apply` -> CMake build, ctest,
      `jm test` (build + import + pytest), and every defined global in
      lib<pkg> starts with the prefix.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

import _gh1653_fixture as FX
from _jmrun import run_cli

P = FX.PREFIX


def _run(cmd, cwd):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True
    )
    assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
    return r.stdout


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    root = FX.build(tmp_path_factory.mktemp("g1653b"))
    FX.set_prefix(root)
    for cmd in ("upgrade", "apply"):
        r = run_cli(cmd, cwd=root)
        assert r.returncode == 0, (cmd, r.stdout + r.stderr)
    return root


@pytest.fixture(scope="module")
def built(upgraded):
    b = upgraded / "b"
    _run(["cmake", "-S", ".", "-B", b, "-DBUILD_PYTHON=OFF"], upgraded)
    _run(["cmake", "--build", b], upgraded)
    return b


def test_the_upgraded_tree_builds_and_its_c_tests_pass(upgraded, built):
    _run(["ctest", "--test-dir", built, "--output-on-failure"], upgraded)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="COFF import libraries carry __imp_ stubs, not the export table "
    "this reads; tests/test_gh1591_c_prefix_nm.py documents the same split",
)
def test_every_export_carries_the_prefix(upgraded, built):
    assert shutil.which("nm"), "nm is required on this host"
    static = list(built.rglob("libq.a"))
    assert static, sorted(built.rglob("libq*"))
    syms = set()
    for line in _run(["nm", "-g", static[0]], upgraded).splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] not in ("U", "w", "v"):
            name = parts[2]
            if sys.platform == "darwin" and name.startswith("_"):
                name = name[1:]
            syms.add(name)
    assert f"{P}_lo_steps" in syms, sorted(syms)
    assert f"{P}_mixer_create" in syms, sorted(syms)
    bad = sorted(
        s for s in syms if not s.startswith(f"{P}_") and s != "q_version"
    )
    assert not bad, bad


def test_the_upgraded_tree_imports_and_its_python_tests_pass(upgraded):
    r = run_cli("test", cwd=upgraded)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
