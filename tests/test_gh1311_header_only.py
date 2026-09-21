"""A component whose C is entirely `static inline` in its header (gh-1311).

doppler's ring buffer is a macro template: three symbol families, one body,
**no `.c` file at all**. jm had no way to say that, so the module stayed
`no_generate` and its binding was hand-written across four faces.

Two things follow from having no `_core.c`, and the second is the one that
bites:

* the sacred header must carry **definitions**, not prototypes -- a scaffold
  that declares `create()` and defines it nowhere does not link, and "no
  foot-guns, all green from day one" means the untouched scaffold builds;
* the CMake core library must be **INTERFACE**, because an OBJECT library
  with no sources is a hard *configure* error, and nothing may fold it into
  `lib<pkg>.so` -- `$<TARGET_OBJECTS:>` on an INTERFACE target is a configure
  error too.

The second has two halves that are easy to mistake for one: creation EMITS
the wiring line, `apply` DETECTS and removes it. Wiring only the detecting
half looks correct until you build a freshly created project.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
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


#: Both manifest layouts, because they use DIFFERENT writers and only one of
#: them was covered. `jm new` defaults to split fragments (`objects/*.toml`)
#: -- what real projects have -- while `_new.run`'s API default is the
#: single-file monolith. A fixture built on the API default never generated
#: the layout whose writer drops an unlisted key, so sabotaging that writer
#: left every test green. Measured, and the reason this is parameterised.
LAYOUTS = (True, False)


def _project(
    root: Path, header_only: bool = True, fragments: bool = True
) -> Path:
    _silent(new_run, "p", root, fragments=fragments)
    _silent(
        object_run,
        root,
        "ring",
        None,
        state_vars=[("g", "double", "1.0")],
        header_only=header_only,
    )
    return root


class TestNoCoreDotC:
    def test_the_source_file_is_not_written(self, tmp_path):
        root = _project(tmp_path / "p")
        assert not (root / "native/src/ring/ring_core.c").exists()

    def test_an_ordinary_component_still_gets_one(self, tmp_path):
        """The guard against over-fixing: suppressing it for everyone would
        satisfy the test above by deleting the normal shape."""
        root = _project(tmp_path / "p", header_only=False)
        assert (root / "native/src/ring/ring_core.c").exists()

    @pytest.mark.parametrize("fragments", LAYOUTS, ids=["split", "monolith"])
    def test_the_key_round_trips(self, tmp_path, fragments):
        """A key `_dump` drops is silently absent by the time anything
        renders -- gh-542, gh-588 and gh-1117 are each that bug.

        Both layouts, because they are written by different code and the
        split one is what `jm new` actually produces.
        """
        root = _project(tmp_path / "p", fragments=fragments)
        assert C.is_header_only(C.load(root), "ring")

    @pytest.mark.parametrize("fragments", LAYOUTS, ids=["split", "monolith"])
    def test_the_key_reaches_the_manifest_on_disk(self, tmp_path, fragments):
        """Not just `load()`: the key must be WRITTEN, or the next command
        reads a manifest that never mentioned it."""
        root = _project(tmp_path / "p", fragments=fragments)
        written = "".join(
            f.read_text()
            for f in list(root.glob("*.toml"))
            + list((root / "objects").glob("*.toml"))
        )
        assert "header_only" in written, written[:400]

    @pytest.mark.parametrize("fragments", LAYOUTS, ids=["split", "monolith"])
    def test_apply_does_not_put_it_back(self, tmp_path, fragments):
        """`apply` replays from the manifest; a flag the replay drops
        regenerates the very file the manifest asked not to have -- and here
        that also restores an OBJECT library with no sources."""
        root = _project(tmp_path / "p", fragments=fragments)
        _silent(apply_run, root)
        assert not (root / "native/src/ring/ring_core.c").exists()


class TestTheHeaderCarriesDefinitions:
    """Declarations would be promises nothing keeps."""

    def test_no_dangling_prototype(self, tmp_path):
        root = _project(tmp_path / "p")
        h = (root / "native/inc/ring/ring_core.h").read_text()
        # Every function the header names must be defined in it.
        for fn in ("ring_create", "ring_destroy", "ring_reset", "ring_steps"):
            assert f"{fn}(" in h, fn
            assert f"{fn}(state);" not in h, f"{fn} is declared, not defined"

    def test_no_duplicate_static(self, tmp_path):
        """The two-line spelling puts the return type and the name on
        separate lines and BOTH look like a definition start; prefixing each
        gives `static inline static`, which does not compile."""
        root = _project(tmp_path / "p")
        h = (root / "native/inc/ring/ring_core.h").read_text()
        assert "static inline static" not in h
        assert "static static" not in h


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheHeaderHasNoExternalLinkage:
    """Two translation units including it must LINK.

    Checked with a compiler rather than by scanning for `static`, because
    the scan is the same traversal the emitter uses and would agree with it
    by construction. A definition with external linkage in a header is a
    duplicate symbol the moment two objects are linked together -- which is
    the whole reason a header-only core is `static inline`.
    """

    def test_two_translation_units_link(self, tmp_path):
        root = _project(tmp_path / "p")
        inc = root / "native" / "inc"
        for n in (1, 2):
            (tmp_path / f"tu{n}.c").write_text(
                '#include "ring/ring_core.h"\n'
                f"int tu{n}(void) {{ return (int)sizeof(ring_state_t); }}\n"
            )
        (tmp_path / "main.c").write_text(
            "int tu1(void); int tu2(void);\n"
            "int main(void) { return tu1() + tu2() > 0 ? 0 : 1; }\n"
        )
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        r = subprocess.run(
            [
                cc,
                "-std=c99",
                f"-I{inc}",
                "-o",
                str(tmp_path / "a.out"),
                str(tmp_path / "tu1.c"),
                str(tmp_path / "tu2.c"),
                str(tmp_path / "main.c"),
                "-lm",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0, r.stdout + r.stderr


class TestTheCoreLibraryIsInterface:
    def test_interface_not_object(self, tmp_path):
        root = _project(tmp_path / "p")
        cmake = (root / "native/src/ring/CMakeLists.txt").read_text()
        assert "add_library(ring_core INTERFACE)" in cmake, cmake
        assert "add_library(ring_core OBJECT" not in cmake

    def test_an_ordinary_component_stays_object(self, tmp_path):
        root = _project(tmp_path / "p", header_only=False)
        cmake = (root / "native/src/ring/CMakeLists.txt").read_text()
        assert "add_library(ring_core OBJECT ring_core.c)" in cmake

    def test_it_is_not_folded_into_the_combined_library(self, tmp_path):
        """`$<TARGET_OBJECTS:>` on an INTERFACE target is a CONFIGURE error,
        so this is not a tidiness point -- the project would not build.

        Creation EMITS the wiring line and `apply` DETECTS and removes it.
        Wiring only the second half looks correct until a freshly created
        project is configured, which is how this was found.
        """
        root = _project(tmp_path / "p")
        top = (root / "CMakeLists.txt").read_text()
        assert "$<TARGET_OBJECTS:ring_core>" not in top, top

    def test_an_ordinary_component_is_folded_in(self, tmp_path):
        root = _project(tmp_path / "p", header_only=False)
        top = (root / "CMakeLists.txt").read_text()
        assert "$<TARGET_OBJECTS:ring_core>" in top


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestTheUntouchedScaffoldBuildsAndRuns:
    """Every defect in this feature was found by building, not by reading:
    an empty component name in the inline bodies, a `steps()` left declared
    and undefined, `duplicate 'static'`, and the wiring line that made
    configure fail outright. None is visible in a string comparison.
    """

    @staticmethod
    def _built(root: Path):
        _project(root)
        c = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert c.returncode == 0, c.stdout[-3000:] + c.stderr[-3000:]
        b = subprocess.run(
            ["cmake", "--build", str(root / "build")],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert b.returncode == 0, b.stdout[-3000:] + b.stderr[-3000:]
        return root

    def test_it_configures_and_builds(self, tmp_path):
        self._built(tmp_path / "p")

    def test_the_ctest_passes(self, tmp_path):
        root = self._built(tmp_path / "p")
        r = subprocess.run(
            [
                "ctest",
                "--test-dir",
                str(root / "build"),
                "--output-on-failure",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert r.returncode == 0, r.stdout[-3000:]

    def test_the_python_face_works(self, tmp_path):
        root = self._built(tmp_path / "p")
        probe = root / "probe.py"
        probe.write_text(
            "import numpy as np\n"
            "from p.ring import Ring\n"
            "r = Ring(g=2.0)\n"
            "assert r.get_g() == 2.0\n"
            "assert r.step(1 + 0j) == 1 + 0j\n"
            "assert r.steps(np.ones(3, dtype=np.complex64)).size == 3\n"
            "r.set_g(3.0)\n"
            "assert r.get_g() == 3.0\n"
            "r.reset()\n"
            "assert r.get_g() == 1.0\n"
            "print('OK')\n"
        )
        out = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "OK" in out.stdout


#: Signatures :class:`TestEveryCoreLibraryEmitterAgrees` knows how to drive.
#: A `header_only` emitter whose shape is not here fails the sweep loudly --
#: widening this is the sanctioned response, exempting the emitter is not.
_CALLABLE_SHAPES = (
    {"component", "header_only"},
    {"header_only"},
    {"component", "items", "header_only", "include"},
)


class TestEveryCoreLibraryEmitterAgrees:
    """Registration-free: the OBJECT/INTERFACE decision has TWO emitters.

    `_render.object_core_decl` serves the collocated shape (a module whose
    leaf name is also one of its objects) and `_render.component_core_decl`
    serves the standalone/module one. They are separate functions for the
    same reason their templates are separate files -- different wording and
    wrapping -- and jm's peer-implementation history says a fix applied to
    one of a pair silently leaves the other wrong.

    The tests above pin the tree that ONE of them produced. This one asks
    the question of every emitter there is, discovered by SIGNATURE rather
    than by a list, so a third peer added later is covered without anyone
    remembering to register it -- and a signature this cannot call fails
    loudly instead of quietly dropping the emitter from the sweep.

    A stray unreachable paste of one emitter's body inside the other is how
    this was found, so "two copies of the decision" is not hypothetical.
    """

    @staticmethod
    def _emitters():
        import inspect

        from just_makeit import _render

        found = {}
        for name, fn in vars(_render).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            if fn.__module__ != _render.__name__:
                continue
            params = inspect.signature(fn).parameters
            if "header_only" not in params:
                continue
            # A signature this cannot drive is a FAILURE, not a skip: the
            # point of discovering by signature is that nothing falls out
            # of the sweep silently.
            assert set(params) in _CALLABLE_SHAPES, (
                f"{name}{inspect.signature(fn)} takes a shape this gate "
                f"cannot call; widen the gate, do not exempt the emitter"
            )
            found[name] = fn
        return found

    @staticmethod
    def _call(fn, header_only: bool) -> str:
        """Drive *fn* whatever its shape, so nothing falls out of the sweep.

        gh-1432 added two emitters of the same decision that are not
        library DECLARATIONS -- a scope keyword and the link/include line
        that carries it. Widening here rather than exempting them is what
        the assertion above demands, and it is the point: the decision has
        more than one shape, and every shape has to answer the same way.
        """
        import inspect

        shape = set(inspect.signature(fn).parameters)
        if shape == {"component", "header_only"}:
            return fn("ring", header_only=header_only)
        if shape == {"header_only"}:
            return fn(header_only=header_only)
        return fn("ring", ["dep_core"], header_only, include=False)

    def test_the_sweep_is_armed(self):
        """An empty or shrinking sweep passes vacuously, so pin the floor."""
        found = self._emitters()
        assert len(found) >= 2, found

    def test_none_emits_an_object_library_for_a_header_only_core(self):
        for name, fn in self._emitters().items():
            out = self._call(fn, header_only=True)
            if "add_library" in out:
                assert "add_library(ring_core INTERFACE)" in out, (name, out)
                assert "add_library(ring_core OBJECT" not in out, (name, out)
                continue
            # gh-1432: the scope family. `PUBLIC` on an INTERFACE library is
            # not a style difference -- CMake refuses the target, so the
            # project does not configure at all.
            assert "INTERFACE" in out, (name, out)
            assert "PUBLIC" not in out, (name, out)

    def test_every_one_still_emits_object_otherwise(self):
        """The converse, so a gate cannot be satisfied by always saying
        INTERFACE -- which would break every ordinary component."""
        for name, fn in self._emitters().items():
            out = self._call(fn, header_only=False)
            if "add_library" in out:
                assert "add_library(ring_core OBJECT ring_core.c)" in out, (
                    name,
                    out,
                )
                assert "INTERFACE" not in out, (name, out)
                continue
            assert "PUBLIC" in out, (name, out)
            assert "INTERFACE" not in out, (name, out)

    def test_no_emitter_carries_unreachable_code(self):
        """The dead paste that prompted this class was a second copy of the
        very decision the sweep pins -- editing it would have changed
        nothing, and the sweep above cannot see it because it never runs.
        """
        import ast
        import inspect

        from just_makeit import _render

        for name in self._emitters():
            src = inspect.getsource(getattr(_render, name))
            body = ast.parse(textwrap.dedent(src)).body[0].body
            for i, stmt in enumerate(body[:-1]):
                assert not isinstance(stmt, (ast.Return, ast.Raise)), (
                    f"_render.{name}: {len(body) - i - 1} statement(s) after "
                    f"a {type(stmt).__name__} on line {stmt.lineno} can never "
                    f"run"
                )
