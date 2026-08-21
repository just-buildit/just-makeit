"""gh-1060/gh-1061: a gh-1034 module scaffold must compile and link.

0.63.2 fixed the duplicate-target collision (gh-1055), so a module's
`test_`/`bench_<cname>_core` pair reached the compiler for the first time.
Two defects were waiting there, and they are the same defect twice: jm wrote
one artefact from a manifest and a second artefact from the component NAME
ALONE, so the two could not agree.

* **gh-1060** — jm injects an `out_type` out-parameter into the prototype it
  writes and omits it from the call it writes, in the same run. `out_type`
  never enters `params`, so it was invisible to both the argument list and
  the all-scalars guard above it. The zero-parameter case is the sharpest:
  `all([])` is `True`, so `void f(uint8_t *out)` got the call `f()`.
* **gh-1061** — the pair linked `<cname>_core m` and nothing else. Neither
  emitter took the module config as an argument, so a declared dependency had
  no path by which it could reach them.

The checks below DERIVE both sides rather than matching a literal, because a
literal is what let these ship: an argument count is compared against the
prototype jm itself generated, and the pair's link line against the `.so`
link line in the same file. A test asserting "the call has two arguments"
would pass just as happily against a wrong prototype.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _args_in_call(text: str, name: str) -> int | None:
    """How many arguments the scaffold's `(void)name(...)` call passes.

    `None` when the scaffold emits no call at all -- the TODO branch, which
    is a correct answer and not a zero-argument call.
    """
    m = re.search(rf"\(void\){re.escape(name)}\(([^;]*)\);", text)
    if m is None:
        return None
    inner = m.group(1).strip()
    return len([a for a in inner.split(",") if a.strip()]) if inner else 0


def _params_in_prototype(text: str, name: str) -> int:
    """How many parameters jm's own header declares for *name*."""
    m = re.search(rf"^\w[\w \t*]*?\b{re.escape(name)}\(([^;]*)\);", text, re.M)
    assert m is not None, f"no prototype for {name}"
    inner = m.group(1).strip()
    if inner in ("", "void"):
        return 0
    return len(inner.split(","))


def _link_libs(text: str, target: str) -> list[str]:
    """The libraries `target`'s `target_link_libraries` call names.

    Regex over the wrapped call, since cmake-format may put the first
    library on the line after the target name.
    """
    m = re.search(
        rf"target_link_libraries\(\s*{re.escape(target)}\s+PRIVATE\b([^)]*)\)",
        text,
    )
    assert m is not None, f"no link line for {target}\n{text}"
    return [w for w in m.group(1).split() if w]


class TestAnOutTypeFunctionIsNotCalled:
    """gh-1060 — the call must match the prototype jm generated."""

    @pytest.mark.parametrize(
        "params",
        [
            [("beta", "double")],
            [],  # `all([]) is True` -- the sharpest form
        ],
        ids=["with-params", "no-params"],
    )
    def test_the_call_never_disagrees_with_the_prototype(
        self, tmp_path, params
    ):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "dsp")
        _quiet(
            function_run,
            root,
            "taps",
            "dsp",
            params=list(params),
            out_type="float",
            variable_output=True,
        )
        header = (root / "native" / "inc" / "dsp" / "dsp_core.h").read_text(
            encoding="utf-8"
        )
        test_c = (root / "native" / "tests" / "test_dsp_core.c").read_text(
            encoding="utf-8"
        )
        declared = _params_in_prototype(header, "taps")
        passed = _args_in_call(test_c, "taps")
        # Either jm declines to call it, or it calls it correctly. What it
        # may never do is emit a call short of its own prototype.
        assert passed is None or passed == declared, (
            f"prototype takes {declared}, scaffold passes {passed}"
        )
        assert "taps" in test_c, "the function is not mentioned at all"

    def test_a_plain_scalar_function_is_still_called(self, tmp_path):
        """The guard against over-fixing.

        Suppressing every call would satisfy the assertion above by deleting
        the smoke test gh-1034 exists to provide.
        """
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "dsp")
        _quiet(
            function_run,
            root,
            "gain",
            "dsp",
            params=[("x", "double")],
            return_type="double",
        )
        test_c = (root / "native" / "tests" / "test_dsp_core.c").read_text(
            encoding="utf-8"
        )
        assert _args_in_call(test_c, "gain") == 1


class TestTheModulePairLinksWhatTheSoLinks:
    """gh-1061 — one rule for both kinds of test target."""

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "win")
        _quiet(
            function_run,
            root,
            "window",
            "win",
            params=[("n", "int")],
            return_type="double",
        )
        _quiet(module_run, root, "dsp", extra_link_libs=["win_core"])
        _quiet(
            function_row := function_run,
            root,
            "gain",
            "dsp",
            params=[("x", "double")],
            return_type="double",
        )
        del function_row
        return root

    def test_the_pair_carries_every_declared_dependency(self, tmp_path):
        root = self._project(tmp_path)
        text = (root / "native" / "src" / "dsp" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        # Derived from the same file, so the two cannot drift apart: whatever
        # the .so links (minus Python) is what the pair must link.
        so_libs = [
            lib
            for lib in _link_libs(text, "dsp")
            if not lib.startswith("Python3::")
        ]
        assert "win_core" in so_libs, so_libs
        for target in ("test_dsp_core", "bench_dsp_core"):
            libs = _link_libs(text, target)
            missing = [lib for lib in so_libs if lib not in libs]
            assert not missing, f"{target} is missing {missing}"

    def test_a_module_with_no_dependency_is_unchanged(self, tmp_path):
        """No churn for free: the one-line form is preserved exactly."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "dsp")
        _quiet(
            function_row := function_run,
            root,
            "gain",
            "dsp",
            params=[("x", "double")],
            return_type="double",
        )
        del function_row
        text = (root / "native" / "src" / "dsp" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert (
            "target_link_libraries(test_dsp_core PRIVATE dsp_core m)" in text
        )
        assert (
            "target_link_libraries(bench_dsp_core PRIVATE dsp_core m)" in text
        )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestItActuallyBuilds:
    """The symptom, end to end.

    Neither defect is visible in the emitted string on its own -- the call
    reads fine until the prototype is beside it, and the link line reads fine
    until a symbol needs resolving. Only the built artefact catches them,
    which is why this test compiles rather than inspects.
    """

    def test_a_module_with_an_out_type_fn_and_a_sibling_dep_builds(
        self, tmp_path
    ):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "win")
        _quiet(function_run, root, "window", "win", params=[("n", "int")])
        _quiet(module_run, root, "dsp", extra_link_libs=["win_core"])
        _quiet(
            function_run,
            root,
            "taps",
            "dsp",
            params=[("beta", "double")],
            out_type="float",
            variable_output=True,
        )
        _quiet(function_run, root, "reset_all", "dsp")
        # The sacred bodies are the author's; write ones that make `dsp`
        # genuinely depend on `win`, which is the shape gh-1061 is about.
        (root / "native" / "src" / "win" / "window.c").write_text(
            '#include "win/win_core.h"\n\n'
            "void\nwindow(int n)\n{\n    (void)n;\n}\n",
            encoding="utf-8",
        )
        (root / "native" / "src" / "dsp" / "reset_all.c").write_text(
            '#include "dsp/dsp_core.h"\n#include "win/win_core.h"\n\n'
            "void\nreset_all(void)\n{\n    window(1);\n}\n",
            encoding="utf-8",
        )
        build = root / "build"
        cfg = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(build), "-DBUILD_PYTHON=OFF"],
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stdout + cfg.stderr
        made = subprocess.run(
            ["cmake", "--build", str(build)],
            capture_output=True,
            text=True,
        )
        assert made.returncode == 0, made.stdout + made.stderr
        # The two executables this is all about must exist and run.
        for exe in ("test_dsp_core", "bench_dsp_core"):
            found = list(build.rglob(exe))
            assert found, f"{exe} was never linked"
