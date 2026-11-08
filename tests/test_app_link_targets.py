"""A C app links C libraries only -- never a Python extension module.

gh-2556's gate went red on macOS alone: the C apps over a module named `m`
failed to link with *unsupported mach-o filetype ... m.cpython-*-darwin.so*.
The app asked for libm as `$<$<NOT:$<PLATFORM_ID:Windows>>:m>`, the bare
name `m` inside a generator expression, and a bare name resolves to a CMake
TARGET first -- `jm module m`'s extension. macOS refuses the MH_BUNDLE; Linux
links the module DSO without a word. It is gh-1305's shadow, in the one
emitter (`_app._LIBM`) that fix did not reach, and the bare-`m` scan there
compares whole link items, so `...:m>` slipped past it.

A function app over a dotted module (`dsp.filters`) linked `dsp.filters_core`
beside it: not a target at all, so CMake took it for a library name, the
core's include path never reached the app, and the build failed on its
header.

So the property is asserted on the generated CMake rather than per symptom:
every item an app links is the libm reference (`_render.LIBM_REF`) or a
library the tree DECLARES with `add_library` -- and a CPython extension is
declared with `Python3_add_library`, so it is never one. Registration-free:
the apps are read from the root's `add_executable` lines and the libraries
from every CMakeLists the tree holds. On Linux, where the link itself would
pass, this is the gate; the compiled run below is the macOS-shaped proof.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _render as R

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Every C app shape: an object app over a module object, over a standalone
#: object, a function app over a flat and over a dotted module -- each module
#: named so its extension target (`m`) is the name libm used to be asked by.
_CALLS = [
    "module m",
    "object o --module m --arg-type float --return-type float",
    "object solo --arg-type float --return-type float",
    "function sq --module m --param x:double --return-type double",
    "module dsp.filters",
    "function scale --module dsp.filters --param x:double"
    " --return-type double",
    "app --target c --object o --module m --name oapp",
    "app --target c --object solo --name sapp",
    "app --target c --module m --function sq --name fapp",
    "app --target c --module dsp.filters --function scale --name dapp",
]
_APPS = ["oapp", "sapp", "fapp", "dapp"]

_CALL = re.compile(r"\b(\w+)\s*\((.*?)\)", re.DOTALL)
_SCOPE = {"PRIVATE", "PUBLIC", "INTERFACE"}


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("applinks")
    assert run_cli("new", "p", cwd=tmp).returncode == 0
    root = tmp / "p"
    for call in _CALLS:
        out = run_cli(*call.split(), cwd=root)
        assert out.returncode == 0, call + "\n" + out.stdout + out.stderr
    return root


def _calls(root: Path) -> "list[tuple[str, list[str]]]":
    """Every CMake command in the tree as (name, args), comments stripped."""
    out = []
    for cmake in root.rglob("CMakeLists.txt"):
        text = "\n".join(
            ln.split("#", 1)[0] for ln in cmake.read_text().splitlines()
        )
        out += [(n, a.split()) for n, a in _CALL.findall(text)]
    return out


def test_every_app_links_only_c_libraries(project: Path) -> None:
    calls = _calls(project)
    declared = {a[0] for n, a in calls if n == "add_library" and a}
    extensions = {a[0] for n, a in calls if n == "Python3_add_library" and a}
    apps = {a[0] for n, a in calls if n == "add_executable" and a}
    # A scan that found no app, or no extension to avoid, proves nothing.
    assert set(_APPS) <= apps, sorted(apps)
    assert "m" in extensions, sorted(extensions)
    bad = []
    for name, args in calls:
        if name != "target_link_libraries" or not args or args[0] not in apps:
            continue
        for item in args[1:]:
            if item in _SCOPE or item == R.LIBM_REF:
                continue
            if item not in declared or item in extensions:
                bad.append(f"{args[0]} links {item!r}")
    assert not bad, "\n".join(bad)


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_app_builds(project: Path) -> None:
    build = project / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(project), "-B", str(build)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert cfg.returncode == 0, cfg.stdout + cfg.stderr
    for app in _APPS:
        out = subprocess.run(
            ["cmake", "--build", str(build), "--target", app],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert out.returncode == 0, app + "\n" + out.stdout + out.stderr
