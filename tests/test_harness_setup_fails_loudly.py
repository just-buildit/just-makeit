"""A run that cannot set up fails, naming why; a killed run leaves nothing.

Found reviewing #2865: ``/tmp`` ran out of inodes (gigabytes free, every
inode used), and an end-to-end gate read GREEN with its fix sabotaged.

**The green.** Each xdist worker provisions a private environment in
``pytest_configure`` (gh-879). On ENOSPC that raises, so the worker dies
before it is handed a test; xdist replaces it, the replacement dies the
same way, and once the restarts run out the controller prints ``no tests
ran`` and exits **0**. `conftest.pytest_testnodedown` now records every
worker that goes down with an error, and `conftest.pytest_sessionfinish`
fails the run on any, printing the ENOSPC sentence `conftest._setup_error`
writes when that is the cause.

**The leak.** A worker's environment (~600 files) was removed in
``pytest_unconfigure``, which a killed run never reaches: 72 of them sat in
one ``/tmp``. Each is now named for its worker's pid, and every worker
reaps the ones whose pid is gone before it provisions its own
(`conftest._reap_stale_worker_envs`).

Each gate runs a child pytest with ``tests/conftest.py`` loaded as a plugin
(``-p conftest``) over a throwaway test file, in its own ``TMPDIR``, so what
it observes is the real harness and nothing a peer worker shares.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _compilers import POSIX_ONLY

TESTS = Path(__file__).parent
SRC = TESTS.parent / "src"


def _env(tmp: Path, extra_path: "list[Path]" = ()) -> dict:
    """The child's environment: a controller of its own, not a worker of
    ours, with its own TMPDIR."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "COV_CORE"))
    }
    (tmp / "t").mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp / "t")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(p) for p in extra_path] + [str(TESTS), str(SRC)]
    )
    return env


def _child(tmp: Path, body: str, *args: str, env: dict, **kw):
    """Run ``pytest -n 2`` over a file holding *body*, under the suite's
    conftest."""
    test = tmp / "test_child.py"
    test.write_text(body, encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "conftest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-n",
        "2",
        "--rootdir",
        str(tmp),
        *args,
        str(test),
    ]
    return subprocess.Popen(
        cmd,
        cwd=tmp,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **kw,
    )


def _run(tmp: Path, body: str, *args: str, env: dict) -> "tuple[int, str]":
    proc = _child(tmp, body, *args, env=env)
    out, _ = proc.communicate(timeout=600)
    return proc.returncode, out


def _assert_fails_naming_enospc(rc: int, out: str) -> None:
    assert rc != 0, f"a run whose workers could not set up exited 0:\n{out}"
    assert "xdist workers went down" in out, out
    assert "out of space or out of inodes (ENOSPC)" in out, out


def test_uv_out_of_space_fails_the_run(tmp_path):
    """uv cannot seed the worker's venv, as on an exhausted ``/tmp``."""
    if os.name == "nt":
        pytest.skip(f"{POSIX_ONLY} a /bin/sh stub stands in for uv")
    stub = tmp_path / "bin"
    stub.mkdir()
    uv = stub / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        "echo 'error: Failed to install seed packages' >&2\n"
        "echo '  cause: No space left on device (os error 28)' >&2\n"
        "exit 2\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = _env(tmp_path)
    env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
    rc, out = _run(tmp_path, "def test_ok():\n    pass\n", env=env)
    _assert_fails_naming_enospc(rc, out)


def test_mkdtemp_out_of_inodes_fails_the_run(tmp_path):
    """The worker cannot even create its directory."""
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "_jm_enospc.py").write_text(
        "import errno, tempfile\n"
        "_real = tempfile.mkdtemp\n"
        "def _mkdtemp(suffix=None, prefix=None, dir=None):\n"
        "    if (prefix or '').startswith('jm-'):\n"
        "        raise OSError(errno.ENOSPC, 'No space left on device')\n"
        "    return _real(suffix, prefix, dir)\n"
        "tempfile.mkdtemp = _mkdtemp\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, [plug])
    rc, out = _run(
        tmp_path, "def test_ok():\n    pass\n", "-p", "_jm_enospc", env=env
    )
    _assert_fails_naming_enospc(rc, out)


def _envs(tmp: Path) -> "set[Path]":
    return set((tmp / "t").glob("jm-gw*-env-*"))


def test_a_killed_run_leaves_no_worker_env(tmp_path):
    """SIGKILL a run mid-test: its workers' environments are left (the
    premise), and the next run's workers reap them."""
    if os.name == "nt":
        pytest.skip(f"{POSIX_ONLY} a process group is killed with killpg")
    env = _env(tmp_path)
    marker = tmp_path / "started"
    body = (
        "import pathlib, time\n"
        "def test_a():\n"
        f"    pathlib.Path({str(marker)!r}).touch()\n"
        "    time.sleep(600)\n"
        "def test_b():\n"
        "    time.sleep(600)\n"
    )
    proc = _child(tmp_path, body, env=env, start_new_session=True)
    try:
        deadline = time.monotonic() + 300
        while not marker.exists():
            assert proc.poll() is None, proc.communicate()[0]
            assert time.monotonic() < deadline, "the child never started"
            time.sleep(0.2)
        left = _envs(tmp_path)
        assert len(left) == 2, left  # one per worker
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=60)
    assert _envs(tmp_path) == left, "the premise: a killed run leaks"

    rc, out = _run(tmp_path, "def test_ok():\n    pass\n", env=env)
    assert rc == 0, out
    assert not _envs(tmp_path), (
        "a killed run's worker environments outlived the next run",
        _envs(tmp_path),
    )


def test_a_pidless_env_is_reaped_only_once_a_day_old(tmp_path):
    """An environment named without a pid is an older checkout's, and may
    belong to a run still going on a shared ``/tmp``: a fresh one survives
    a run, and one past a day old is reaped."""
    env = _env(tmp_path)
    fresh = tmp_path / "t" / "jm-gw0-env-legacyfresh"
    old = tmp_path / "t" / "jm-gw0-env-legacyold"
    for d in (fresh, old):
        (d / "venv").mkdir(parents=True)
    aged = time.time() - 25 * 3600
    os.utime(old, (aged, aged))
    rc, out = _run(tmp_path, "def test_ok():\n    pass\n", env=env)
    assert rc == 0, out
    assert fresh.is_dir(), "a live old-style environment was reaped"
    assert not old.exists(), "a day-old pid-less environment survived"
