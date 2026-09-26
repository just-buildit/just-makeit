"""gh-1653, compiled: an upgraded tree whose author C lived in the manifest
and in a `JM_DEFINE_STEPS` call BUILDS, tests, imports and exports only
prefixed symbols.

The source half (tests/test_gh1653_upgrade_manifest_c.py) reads what the
upgrade wrote. This reads what the compiler and linker accept: before
gh-1653 the same tree failed to compile on `lo_state_t` in the mixer's
state struct (a manifest `type`), on `lo_create` in its `create()` (a
manifest `create_impl`), and on `lo_steps` / `lo_step_batch` the macro
pasted from its unmoved `lo` argument.

GATE: set `c_prefix` -> `jm upgrade` -> the mixer's sacred files rendered
      again from its manifest by `jm apply` -> CMake build, ctest,
      `jm test` (build + import + pytest), and every defined global in
      lib<pkg>, shared and static, starts with the prefix (read by
      tests/_exports.py, on Windows too -- gh-1648).
"""

from __future__ import annotations

import subprocess

import pytest

import _exports as EX
import _gh1653_fixture as FX
from _jmrun import run_cli

P = FX.PREFIX


def _run(cmd, cwd):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True
    )
    return r.returncode, r.stdout[-3000:] + r.stderr[-3000:]


# The fixtures record every step and assert nothing: a step failing inside a
# fixture reports as a setup ERROR, which names no test, so a sabotage that
# breaks the build would read as the gate never having run (gh-1430). Each
# test asserts the steps it needs instead.


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    root = FX.build(tmp_path_factory.mktemp("g1653b"))
    FX.set_prefix(root)
    steps = []
    r = run_cli("upgrade", cwd=root)
    steps.append(("upgrade", r.returncode, r.stdout + r.stderr))
    # The mixer's author C lives ONLY in its manifest. On the upgraded tree
    # its sacred files already exist, and the C-file respell (phase 3) fixes
    # them, so a stale manifest is latent: it bites whenever jm renders from
    # the manifest again -- here `apply` restoring files that are missing,
    # the path the issue measured. Deleting them makes the manifest the
    # only source of the mixer's C.
    for rel in FX.MIXER_SACRED:
        (root / rel).unlink()
    r = run_cli("apply", cwd=root)
    steps.append(("apply", r.returncode, r.stdout + r.stderr))
    for rel in FX.MIXER_SACRED:
        steps.append((f"{rel} restored", int(not (root / rel).is_file()), ""))
    return root, steps


@pytest.fixture(scope="module")
def built(upgraded):
    root, steps = upgraded
    b = root / "b"
    steps = list(steps)
    for cmd in (
        ["cmake", "-S", ".", "-B", b, "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", b],
        ["ctest", "--test-dir", b, "--output-on-failure"],
    ):
        rc, out = _run(cmd, root)
        steps.append((cmd[:2], rc, out))
        if rc:
            break
    return root, b, steps


def _ok(steps):
    for what, rc, out in steps:
        assert rc == 0, (what, out)


def test_the_upgraded_tree_builds_and_its_c_tests_pass(built):
    _ok(built[2])


def test_every_export_carries_the_prefix(built):
    _, b, steps = built
    _ok(steps)
    syms = EX.exports(b, "q")
    assert f"{P}_lo_steps" in syms, sorted(syms)
    assert f"{P}_mixer_create" in syms, sorted(syms)
    bad = sorted(
        s for s in syms if not s.startswith(f"{P}_") and s != "q_version"
    )
    assert not bad, bad


def test_the_upgraded_tree_imports_and_its_python_tests_pass(upgraded):
    root, steps = upgraded
    _ok(steps)
    r = run_cli("test", cwd=root)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
