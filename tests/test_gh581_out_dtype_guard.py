"""gh-581: an ``out=`` buffer must never be silently cast into a temporary.

Passing ``out=`` means "write into THIS array, do not allocate". Marshaling it
with a bare ``PyArray_FROM_OTF(out_obj, NPY_X, … | NPY_ARRAY_WRITEABLE)`` breaks
that promise whenever the dtype does not already match: ``FROM_OTF`` casts into
a NEW temporary, the kernel fills the temporary, and the temporary is freed on
the way out. The call returns a correct-looking result while the caller's buffer
is never touched — invisible to anyone who only reads the return value.

The handle generator (:mod:`_handle`, shape (d)) and the capsule generator
(:mod:`_capsule`) already guarded against this; the object generator's ``out=``
paths and the module-function generator's ``out`` params did not, so the same
class silently lost the check when its ``kind`` changed. The guard now lives
once in :func:`_coerce.out_buffer_guard` and every generator emits it.

The load-bearing test here is :class:`TestNoUnguardedWritableMarshal`, which
asserts the *invariant* rather than today's site list: any future ``out=`` path
that forgets the guard fails it without anyone having to remember gh-581.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _coerce  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

# A caller-supplied buffer is marshaled with the WRITEABLE flag; the guard is
# the PyArray_Check/PyArray_TYPE/ISWRITEABLE triple that must precede it.
WRITABLE_MARSHAL = re.compile(
    r"PyArray_FROM_OTF\(\s*[^;]*?NPY_ARRAY_WRITEABLE", re.S
)
GUARD_HEAD = "if (!PyArray_Check("
ERR_MSG = "must be a writable ndarray of the output dtype"


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run(
        "dsp",
        dest,
        ["nco"],
        [("freq", "double", "0.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return dest


def _ext(project, obj="nco"):
    return (project / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


class TestEmitter:
    """The shared emitter itself — one primitive, no per-generator copies."""

    def test_shape(self):
        g = _coerce.out_buffer_guard("out_obj", "NPY_COMPLEX64")
        assert GUARD_HEAD + "out_obj)" in g
        assert "PyArray_TYPE((PyArrayObject *)out_obj) != NPY_COMPLEX64" in g
        assert "!PyArray_ISWRITEABLE((PyArrayObject *)out_obj)" in g
        assert f'"out {ERR_MSG}"' in g
        assert g.endswith("}\n")

    def test_decrefs_release_before_return(self):
        """Anything already owned is released on the reject path."""
        g = _coerce.out_buffer_guard(
            "out_obj", "NPY_FLOAT32", decrefs="Py_DECREF(in_arr);"
        )
        assert g.index("Py_DECREF(in_arr);") < g.index("return NULL;")

    def test_label_names_the_users_argument(self):
        g = _coerce.out_buffer_guard("dst_obj", "NPY_FLOAT32", label="dst")
        assert f'"dst {ERR_MSG}"' in g

    @pytest.mark.parametrize("indent", [4, 8])
    def test_fits_line_budget_at_either_depth(self, indent):
        """79-char project style, at function scope and inside an if-branch."""
        g = _coerce.out_buffer_guard(
            "out_obj", "NPY_COMPLEX64", indent=" " * indent
        )
        assert all(len(ln) <= 79 for ln in g.splitlines())
        assert all(ln.startswith(" " * indent) for ln in g.splitlines())


class TestObjectSteps:
    """The built-in ``steps(x, out=)`` path (the shape doppler hit)."""

    def test_guard_precedes_marshal(self, project):
        ext = _ext(project)
        branch = ext[ext.index("out_obj && out_obj != Py_None") :]
        assert branch.index(GUARD_HEAD) < branch.index("PyArray_FROM_OTF")

    def test_requires_exact_output_dtype(self, project):
        ext = _ext(project)
        assert "PyArray_TYPE((PyArrayObject *)out_obj) != NPY_COMPLEX64" in ext

    def test_releases_input_on_reject(self, project):
        """The input array is already owned when the guard runs."""
        ext = _ext(project)
        guard = ext[ext.index(GUARD_HEAD) :]
        assert guard.index("Py_DECREF(in_arr);") < guard.index("return NULL;")


class TestMethodOutPaths:
    """Both method ``out=`` flavors — fixed-size batch and variable_output."""

    def test_batch_method(self, project):
        method_run(
            project,
            "nco",
            "gain",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            batch=True,
        )
        assert GUARD_HEAD in _ext(project)

    def test_variable_output_method(self, project):
        method_run(
            project,
            "nco",
            "decim",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        ext = _ext(project)
        branch = ext[ext.index("out_obj && out_obj != Py_None") :]
        assert branch.index(GUARD_HEAD) < branch.index("PyArray_FROM_OTF")


class TestFunctionOutParam:
    """The module-function generator's ``out``-marked array param."""

    def test_guard_emitted(self, project):
        module_run(project, "ops")
        function_run(
            project,
            "scale",
            "ops",
            params=[("x", "float[]", False), ("y", "float[]", True)],
            return_type="void",
        )
        src = (project / "native" / "src" / "ops" / "ops_ext.c").read_text(
            encoding="utf-8"
        )
        assert GUARD_HEAD + "y_obj)" in src
        # the message names the param as the user typed it, not a generic "out"
        assert f'"y {ERR_MSG}"' in src
        assert src.index(GUARD_HEAD) < src.index("NPY_ARRAY_WRITEABLE")


class TestNoUnguardedWritableMarshal:
    """The invariant: no writable marshal anywhere without a preceding guard.

    This is what protects future generators — a new ``out=`` path that forgets
    the guard fails here even though it is not named anywhere in this file.
    """

    def _sources(self, project):
        method_run(
            project,
            "nco",
            "decim",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        method_run(
            project,
            "nco",
            "gain",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            batch=True,
        )
        module_run(project, "ops")
        function_run(
            project,
            "scale",
            "ops",
            params=[("x", "float[]", False), ("y", "float[]", True)],
            return_type="void",
        )
        return sorted((project / "native").rglob("*_ext.c"))

    def test_every_writable_marshal_is_guarded(self, project):
        srcs = self._sources(project)
        assert srcs, "fixture produced no extension sources"
        seen = 0
        for path in srcs:
            text = path.read_text(encoding="utf-8")
            for m in WRITABLE_MARSHAL.finditer(text):
                seen += 1
                # The guard is emitted immediately above its marshal, so it is
                # the LAST guard occurring before this match.
                before = text[: m.start()]
                assert GUARD_HEAD in before, (
                    f"{path.name}: writable marshal at offset {m.start()} "
                    "has no preceding exact-dtype guard (gh-581)"
                )
                between = before[before.rindex(GUARD_HEAD) :]
                assert ERR_MSG in between, (
                    f"{path.name}: the guard nearest the writable marshal at "
                    f"offset {m.start()} is not an out-buffer guard (gh-581)"
                )
        assert seen, "no writable out= marshal generated — fixture is stale"
