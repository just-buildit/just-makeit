"""A module named `m` shadowed the math library (gh-1305).

Every generated test and benchmark target links libm, because `jm_bench.h`
and the scaffolded smoke tests call `sqrt`. They asked for it by the bare
name `m`.

A bare name in `target_link_libraries` is resolved as a CMake **target**
first, and only falls through to a system library search when no such target
exists. `jm module m` defines one: `Python3_add_library(m MODULE ...)`. So
the name resolved to the module's own extension DSO, and the two shapes
failed differently:

| shape                                  | symptom                           |
| -------------------------------------- | --------------------------------- |
| `jm module m` + `jm object --module m` | link: `undefined reference to     |
|                                        | sqrt`                             |
| `jm module m` + `jm function`          | configure: *Target "m" of type    |
|                                        | MODULE_LIBRARY may not be linked  |
|                                        | into another target*              |

The filed issue measured the first. The second is worse -- a hard configure
error, so nothing in the project builds at all -- and it lives in a different
emitter (`_render._module_link_libs`), which is why this file tests the
mechanism per shape rather than testing the issue as filed.

`find_library(JM_MATH_LIBRARY m)` resolves to an absolute path, which no
target name can shadow. The declaration and the reference are one pair
(`_render.LIBM_PREAMBLE` / `_render.LIBM_REF`): a reference without its
declaration expands to the empty string and silently links no libm at all,
which is the same broken build by a quieter route. `TestEveryReference`
below is what keeps them together, and it is registration-free -- it walks
whatever CMakeLists the tree actually contains, so a future emitter is
covered without being added to a list.
"""

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import (  # noqa: E402
    _reconcile_bench_cmake,
    run as apply_run,
)
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


_LINK_CALL = re.compile(
    r"target_link_libraries\s*\((.*?)\)", re.DOTALL | re.MULTILINE
)
_SCOPE = ("PRIVATE", "PUBLIC", "INTERFACE")


def _link_args(text):
    """Every `target_link_libraries` call in *text* as (target, [args]).

    Comments are stripped first. The preamble this fix adds *talks about* the
    bare name `m`, and a scanner that reads its own explanation as code is
    the shape where an EXAMPLE of what a detector looks for blinds it.
    """
    stripped = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    calls = []
    for body in _LINK_CALL.findall(stripped):
        toks = body.split()
        if not toks:
            continue
        calls.append((toks[0], [t for t in toks[1:] if t not in _SCOPE]))
    return calls


def _every_shape(dest):
    """A project carrying every shape that emits a libm link line.

    Standalone object, module object, collocated object (object name ==
    module name), and a module's OWN pair -- the last reaching
    `_render.render_module_test_targets`, a different emitter from the
    templates.

    The module's own pair needs a module of its own (`dsp`). A module that
    also holds a collocated object does not render one: the object's pair has
    already claimed `test_<cname>_core`, and `render_module_test_targets`
    skips a target name that is taken. Putting the free function in module
    `m` alongside collocated object `m` therefore rendered NO module pair at
    all -- measured, by sabotaging `_module_link_libs` back to a bare `m` and
    watching this fixture stay green. A sweep is only as good as the tree it
    walks.
    """
    _silent(new_run, "p", dest)
    _silent(
        object_run, dest, "solo", None, state_vars=[("g", "double", "1.0")]
    )
    _silent(module_run, dest, "m")
    _silent(object_run, dest, "o", module="m", state_vars=[("n", "int", "0")])
    _silent(object_run, dest, "m", module="m", state_vars=[("n", "int", "0")])
    _silent(module_run, dest, "dsp")
    _silent(
        function_run,
        dest,
        "scale",
        "dsp",
        params=[("x", "double", "", "")],
        return_type="double",
    )
    return dest


def _configure(root):
    return subprocess.run(
        ["cmake", "-S", str(root), "-B", str(root / "build")],
        capture_output=True,
        text=True,
        timeout=900,
    )


def _build(root, target):
    return subprocess.run(
        ["cmake", "--build", str(root / "build"), "--target", target],
        capture_output=True,
        text=True,
        timeout=900,
    )


def _unfix(cmake):
    """Put the pre-fix text back: bare `m`, and no declaration."""
    text = cmake.read_text()
    assert R.LIBM_REF in text, f"{cmake} has no libm reference to un-fix"
    text = text.replace(R.LIBM_REF, "m")
    text = text.replace("find_library(JM_MATH_LIBRARY m)", "")
    cmake.write_text(text)


class TestNoGeneratedCMakeLinksABareM:
    """The defect itself, at the text level, across every shape."""

    def test_no_bare_m_anywhere(self, tmp_path):
        root = _every_shape(tmp_path / "p")
        offenders = []
        for cmake in sorted(root.rglob("CMakeLists.txt")):
            for target, args in _link_args(cmake.read_text()):
                if "m" in args:
                    offenders.append(
                        f"{cmake.relative_to(root)}: {target} links bare `m`"
                    )
        assert not offenders, "\n".join(offenders)

    def test_the_scan_reaches_the_link_lines_it_claims_to(self, tmp_path):
        """An empty scan would pass the test above for free.

        Absent output is not a pass: this asserts the walk actually reached
        the test and bench pair of every shape, so `test_no_bare_m_anywhere`
        is measuring something.
        """
        root = _every_shape(tmp_path / "p")
        targets = {
            t
            for cmake in root.rglob("CMakeLists.txt")
            for t, _ in _link_args(cmake.read_text())
        }
        for expected in (
            "test_solo_core",  # standalone object
            "bench_solo_core",
            "test_o_core",  # module object
            "bench_o_core",
            "test_m_core",  # collocated object
            "bench_m_core",
            "test_dsp_core",  # the module's OWN pair, a second emitter
            "bench_dsp_core",
        ):
            assert expected in targets, sorted(targets)


class TestEveryReference:
    """A reference to `JM_MATH_LIBRARY` never appears without its
    declaration -- the failure mode this fix could quietly become."""

    def test_reference_implies_declaration(self, tmp_path):
        root = _every_shape(tmp_path / "p")
        seen = 0
        for cmake in sorted(root.rglob("CMakeLists.txt")):
            text = cmake.read_text()
            if R.LIBM_REF not in text:
                continue
            seen += 1
            assert "find_library(JM_MATH_LIBRARY m)" in text, (
                f"{cmake.relative_to(root)} references {R.LIBM_REF} but"
                " never declares it -- it expands to nothing and links no"
                " libm at all"
            )
        assert seen >= 3, f"only {seen} CMakeLists referenced libm"


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheFiledShapeBuilds:
    """`jm module m` + `jm object --module m`: the issue as filed."""

    @staticmethod
    def _project(root):
        _silent(new_run, "p", root)
        _silent(module_run, root, "m")
        _silent(
            object_run, root, "o", module="m", state_vars=[("n", "int", "0")]
        )
        _silent(apply_run, root)
        return root

    def test_the_bench_target_links(self, tmp_path):
        root = self._project(tmp_path / "p")
        assert _configure(root).returncode == 0
        r = _build(root, "bench_o_core")
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_with_a_bare_m_it_does_not(self, tmp_path):
        """Prove the link was the thing at stake.

        The sabotage is applied to the generated CMakeLists rather than to
        jm, so it reproduces the exact text the bug shipped, and the
        assertion names `sqrt` -- a non-zero exit alone would also be
        satisfied by an unrelated build failure.
        """
        root = self._project(tmp_path / "p")
        _unfix(root / "native/src/o/CMakeLists.txt")
        assert _configure(root).returncode == 0
        r = _build(root, "bench_o_core")
        assert r.returncode != 0, r.stdout[-2000:]
        assert "sqrt" in (r.stdout + r.stderr), r.stdout[-2000:]


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheModulesOwnPairBuilds:
    """`jm module m` + a free function: the peer shape.

    This one failed at CONFIGURE, so it is asserted separately -- a build
    assertion alone would have reported the configure error as a build
    failure and said nothing about which of the two was at stake.
    """

    @staticmethod
    def _project(root):
        _silent(new_run, "p", root)
        _silent(module_run, root, "m")
        _silent(
            function_run,
            root,
            "scale",
            "m",
            params=[("x", "double", "", "")],
            return_type="double",
        )
        return root

    def test_it_configures_and_builds(self, tmp_path):
        root = self._project(tmp_path / "p")
        c = _configure(root)
        assert c.returncode == 0, c.stdout[-3000:] + c.stderr[-3000:]
        r = _build(root, "bench_m_core")
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_with_a_bare_m_configure_fails(self, tmp_path):
        root = self._project(tmp_path / "p")
        _unfix(root / "native/src/m/CMakeLists.txt")
        c = _configure(root)
        assert c.returncode != 0, c.stdout[-2000:]
        assert "MODULE_LIBRARY" in (c.stdout + c.stderr), c.stdout[-2000:]


class TestTheBenchReconcilerSeedsItsDeclaration:
    """`_reconcile_bench_cmake` APPENDS to a CMakeLists it does not re-render.

    It is the fourth emitter of this link line, and the only one that writes
    into a file whose existing text it keeps. A project scaffolded before
    this fix carries no `find_library` line, so a reference spliced in beside
    it would expand to the empty string and link no libm at all.

    Called directly rather than through `jm apply`. Measured: driving it via
    `apply` proves nothing, because apply re-renders a component's
    CMakeLists wholesale on the way past -- the preamble comes back through
    the template and the reconciler reports no change at all. That test
    passed with this seeding removed, which is the only reason it is not
    written that way here.
    """

    def test_it_seeds_the_declaration_into_a_file_without_one(self, tmp_path):
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        _silent(
            object_run,
            root,
            "solo",
            None,
            state_vars=[("g", "double", "1.0")],
        )
        cmake = root / "native/src/solo/CMakeLists.txt"
        # The pre-fix shape: bare `m`, no declaration, no bench target.
        text = cmake.read_text()
        text = text[text.index("# OBJECT library") :]
        text = text[: text.index("add_executable(")]
        cmake.write_text(text.replace(R.LIBM_REF, "m"))
        assert "JM_MATH_LIBRARY" not in cmake.read_text()

        cfg = C.load(root)
        changed = _reconcile_bench_cmake(root, cfg)
        assert changed, "the reconciler did not touch the file"

        out = cmake.read_text()
        assert "find_library(JM_MATH_LIBRARY m)" in out
        assert R.LIBM_REF in out[out.index("bench_solo_core") :]

    def test_it_does_not_add_a_second_declaration(self, tmp_path):
        """`find_library` caches, so a duplicate would be harmless -- but a
        reconciler that appends one on every pass is a file that grows."""
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        _silent(
            object_run,
            root,
            "solo",
            None,
            state_vars=[("g", "double", "1.0")],
        )
        cmake = root / "native/src/solo/CMakeLists.txt"
        text = cmake.read_text()
        text = text[: text.index("add_executable(bench_solo_core")]
        cmake.write_text(text)
        assert text.count("find_library(JM_MATH_LIBRARY m)") == 1

        _reconcile_bench_cmake(root, C.load(root))
        assert cmake.read_text().count("find_library(JM_MATH_LIBRARY m)") == 1
