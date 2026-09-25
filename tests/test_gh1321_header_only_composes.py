"""Adding to a header-only component (gh-1321).

gh-1311 scaffolds a component with **no `<comp>_core.c`**. Every command that
writes a C body still targeted that file, so the feature composed with
nothing: `jm method` died with `FileNotFoundError` on the file the feature
deliberately does not create, and `jm property` told the author to implement
the getter there.

**Why the gh-1311 suite missed it.** That fixture creates a header-only object
and stops -- it applies, builds, runs CTest and exercises the Python face, but
never ADDS anything afterwards. Every writer that assumes `_core.c` was out of
frame. Registration-free over files is not coverage over shapes; the shape
that was missing is "the component already exists, now add to it".

The two halves of the fix are not independent and are tested as a pair: the
body moves into the header as `static inline`, AND the prototype must stop
being emitted. A non-static declaration ahead of a `static inline` definition
is `static declaration of 'f' follows non-static declaration`, so relocating
the body alone trades a link error for a compile error.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402


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


def _capture(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **k)
    return buf.getvalue()


def _guidance(out: str) -> str:
    """The `Done!  Implement ...` line alone.

    Asserting over the whole transcript is vacuous here: it lists every file
    touched, and `bench_ring_core.c` CONTAINS `ring_core.c`, so
    `"ring_core.c" not in out` could never hold whatever the guidance said.
    """
    for line in out.splitlines():
        if "Implement" in line:
            return line
    raise AssertionError(f"no guidance line in:\n{out}")


#: Standalone AND module, because gh-1311 only ever generated the first and
#: the second was broken four ways -- including a `$<TARGET_OBJECTS:>` on an
#: INTERFACE library, which is a CONFIGURE error, so the project did not build
#: at all. doppler's ring is a module, so that is the shape that matters.
SHAPES = [None, "bufs"]
SHAPE_IDS = ["standalone", "module"]


def _ring(root: Path, header_only: bool = True, module: str | None = None):
    _silent(new_run, "p", root)
    if module:
        from just_makeit._module import run as module_run

        _silent(module_run, root, module)
    _silent(
        object_run,
        root,
        "ring",
        module,
        state_vars=[("cap", "size_t", "8")],
        arg_type="void",
        return_type="float _Complex",
        header_only=header_only,
    )
    return root


def _add_method(root: Path, name: str = "wait", **kw):
    return _capture(
        method_run,
        root,
        "ring",
        name,
        None,
        "void",
        "float _Complex",
        False,
        [],
        params=[("n", "size_t")],
        borrow=True,
        **kw,
    )


class TestItDoesNotCrash:
    """The reported symptom: a raw traceback, not a refusal."""

    def test_a_method_can_be_added(self, tmp_path):
        _add_method(_ring(tmp_path / "p"))

    def test_an_ordinary_component_is_unaffected(self, tmp_path):
        root = _ring(tmp_path / "p", header_only=False)
        _add_method(root)
        c = (root / "native/src/ring/ring_core.c").read_text()
        assert "ring_wait" in c, c


@pytest.mark.parametrize("module", SHAPES, ids=SHAPE_IDS)
class TestTheBodyGoesIntoTheHeader:
    def test_defined_static_inline(self, tmp_path, module):
        root = _ring(tmp_path / "p", module=module)
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert "ring_wait" not in h
        _add_method(root)
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert "static inline float _Complex *\nring_wait(" in h, h

    def test_no_core_c_is_created(self, tmp_path, module):
        """Writing one would undo gh-1311: an OBJECT library with a source
        again, and a second home for bodies the header already holds. The
        MODULE path wrote it unconditionally, so the tree carried an INTERFACE
        library and a source file that nothing compiles."""
        root = _ring(tmp_path / "p", module=module)
        _add_method(root)
        assert not (root / "native/src/ring/ring_core.c").exists()

    def test_the_prototype_is_suppressed(self, tmp_path, module):
        """The other half. `static inline` definition + non-static prototype
        is a compile error, so relocating the body alone is not a fix.

        The module leg is the one that pins this: it injects declarations
        surgically via `_inject_decls_into_core_h` instead of regenerating the
        header, so it is the path where an unsuppressed prototype actually
        lands beside the definition."""
        root = _ring(tmp_path / "p", module=module)
        _add_method(root)
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert h.count("ring_wait") == 1, h
        assert (
            "float _Complex *ring_wait(ring_state_t *state, size_t n);"
            not in h
        ), h

    def test_the_body_lands_inside_the_extern_c_block(self, tmp_path, module):
        """Outside it, a C++ consumer gets C++ linkage for one function and C
        for the rest -- which links until someone includes it from C++."""
        root = _ring(tmp_path / "p", module=module)
        _add_method(root)
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert h.index("ring_wait(") < h.rindex("#ifdef __cplusplus"), h


class TestTheGuidanceNamesAFileThatExists:
    """Sending the author to a path that cannot exist is the defect, not a
    cosmetic complaint: `_core.c` is where they would otherwise write it."""

    def test_method_names_the_header(self, tmp_path):
        line = _guidance(_add_method(_ring(tmp_path / "p")))
        assert "ring_core.h" in line, line
        assert "ring_core.c" not in line, line

    def test_method_on_an_ordinary_component_names_the_source(self, tmp_path):
        line = _guidance(_add_method(_ring(tmp_path / "p", header_only=False)))
        assert "ring_core.c" in line, line

    def test_property_names_the_header(self, tmp_path):
        root = _ring(tmp_path / "p")
        line = _guidance(
            _capture(
                property_run, root, "ring", "level", None, "double", False
            )
        )
        assert "ring_core.h" in line, line
        assert "ring_core.c" not in line, line

    def test_property_on_an_ordinary_component_names_the_source(
        self, tmp_path
    ):
        root = _ring(tmp_path / "p", header_only=False)
        line = _guidance(
            _capture(
                property_run, root, "ring", "level", None, "double", False
            )
        )
        assert "ring_core.c" in line, line


class TestOneHomeForWhereABodyGoes:
    def test_the_writer_is_shared(self):
        """`_method` had its own copy of 'bodies live in _core.c', which is
        how it kept the pre-gh-1311 answer. The decision has one home now."""
        src = (
            Path(__file__).parent.parent / "src/just_makeit/_method.py"
        ).read_text()
        assert "append_component_body" in src

    def test_staticize_is_not_reimplemented(self):
        """gh-1311's transform is subtle -- it handles the two-line spelling
        that otherwise yields `duplicate 'static'`. A second copy would get
        that wrong differently."""
        init = (
            Path(__file__).parent.parent / "src/just_makeit/_init.py"
        ).read_text()
        assert "from ._context._state import staticize" in init
        assert "def staticize" not in init


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheResultCompilesAndImports:
    """`}size_t` in the generated header LOOKS broken and compiles fine (C
    allows declaration specifiers in any order), so reading the output cannot
    decide this one. Build it."""

    @pytest.mark.parametrize("module", SHAPES, ids=SHAPE_IDS)
    def test_it_builds_and_imports_with_a_method_added(self, tmp_path, module):
        """The module leg is the one that was a CONFIGURE error, and no text
        scan can see it: `$<TARGET_OBJECTS:>` on an INTERFACE library fails
        when cmake evaluates the generator expression, which is why this
        configures a FRESH build dir rather than reading CMakeLists.txt.
        """
        root = _ring(tmp_path / "p", module=module)
        _add_method(root)
        for args in (
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            ["cmake", "--build", str(root / "build")],
        ):
            r = subprocess.run(
                args, capture_output=True, text=True, timeout=900
            )
            assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        code = (
            "import sys, glob\n"
            f"sys.path[:0] = glob.glob({str(root)!r} + '/build*/**/',"
            " recursive=True) + ["
            + repr(str(root / "src"))
            + "]\n"
            + (
                "from p.bufs import Ring\n"
                if module
                else "from p.ring import Ring\n"
            )
            + "r = Ring(cap=8)\n"
            "print('OK', hasattr(r, 'wait'))\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "OK True" in r.stdout, r.stdout
