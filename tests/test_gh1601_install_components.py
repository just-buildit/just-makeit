"""gh-1601: jm's install rules carry the runtime/dev component split.

A distribution builds two packages from one install: ``lib<name>`` holds the
shared library a program loads, ``lib<name>-dev`` everything a build needs.
CMake expresses that as install COMPONENTs, and doppler tagged its own rules
so; jm tagged none, so everything landed in CMake's default component and the
split was impossible without hand-editing the install section -- which since
gh-1589 is jm's managed block.

``runtime`` is the versioned shared library (and a Windows DLL); ``dev`` is
the rest: headers, the static library, the unversioned ``lib<name>.so`` link
(``NAMELINK_COMPONENT``), the CMake package and the ``.pc`` -- including the
install-time step that writes the ``.pc``, or a dev-only install would ship
a stale one. A plain ``cmake --install`` still installs both.

Lives on PROJECT_ENV_TESTS: it builds and installs.

GATE: `cmake --install --component runtime` installs exactly the shared
      library a program loads, `--component dev` everything else, and the
      two together are a plain install.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli


def _run(cmd, cwd):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True
    )
    assert r.returncode == 0, (cmd, r.stdout[-2000:], r.stderr[-2000:])


def _files(prefix: Path) -> "set[str]":
    return {
        p.relative_to(prefix).as_posix()
        for p in prefix.rglob("*")
        if p.is_file() or p.is_symlink()
    }


@pytest.fixture(scope="module")
def installs(tmp_path_factory):
    root = tmp_path_factory.mktemp("gh1601")
    r = run_cli("new", "my_proj", "--object", "g", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    proj, b = root / "my_proj", root / "my_proj" / "b"
    _run(["cmake", "-S", ".", "-B", b, "-DBUILD_PYTHON=OFF"], proj)
    _run(["cmake", "--build", b], proj)
    out = {}
    for comp in ("runtime", "dev", None):
        pfx = root / (comp or "all")
        extra = ["--component", comp] if comp else []
        _run(["cmake", "--install", b, "--prefix", pfx, *extra], proj)
        out[comp or "all"] = _files(pfx)
    return out


def _is_shared(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return ".so." in base or base.endswith((".dylib", ".dll"))


def test_runtime_is_only_the_shared_library(installs):
    runtime = installs["runtime"]
    assert runtime, "the runtime component installed nothing"
    assert all(_is_shared(f) for f in runtime), sorted(runtime)
    # ...and not the unversioned dev link a build links against.
    assert not any(
        f.endswith(("/libmy_proj.so", "/libmy_proj.dylib")) for f in runtime
    ), sorted(runtime)


def test_dev_is_everything_a_build_needs(installs):
    dev = installs["dev"]
    for want in (
        "include/my_proj.h",
        "lib/cmake/my_proj/my_proj-config.cmake",
        "lib/cmake/my_proj/my_proj-config-version.cmake",
        "lib/cmake/my_proj/my_proj-targets.cmake",
        "lib/pkgconfig/my_proj.pc",
    ):
        assert any(f.endswith(want.split("/", 1)[1]) for f in dev), (
            want,
            sorted(dev),
        )
    assert any(f.endswith(".a") or f.endswith(".lib") for f in dev), sorted(
        dev
    )


def test_the_two_are_a_plain_install_and_disjoint(installs):
    assert installs["runtime"].isdisjoint(installs["dev"])
    assert installs["runtime"] | installs["dev"] == installs["all"]
