"""gh-1516: a composer's `extra_methods` compiles through `jm apply`.

gh-1190 shipped the row and the `#include` of `<cname>_ext_extra.c`, gated by
assertions on rendered text. Neither half survived a compiler:

1. **`apply` never emitted the include.** It renders into a pristine replay
   tree and asked *that* tree whether the author's file exists -- it never
   does. `_handle.materialize` takes `project_root=` for the same reason
   (gh-374); the composer now does too.
2. **Even included, the row could not compile.** The `PyMethodDef` row sits in
   the type's method table, rendered with the type; the include has to come
   after the types so a hand-written method can call them. So the row named a
   function its translation unit had not declared yet. jm now forward-declares
   every row's `fn`, with the signature its `flags` imply.

The compiled half of this gate is the `composer_seams` example, which declares
`Mix.total_samples()` in `_ext_extra.c` and calls it from Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from test_composer_apply import _project  # noqa: E402

_EXT = "native/src/wfm_compose/wfm_compose_ext.c"
_EXTRA = "native/src/wfm_compose/wfm_compose_ext_extra.c"
_INCLUDE = '#include "wfm_compose_ext_extra.c"'


def _declare(root: Path, *rows: dict) -> None:
    cfg = C.load(root)
    cfg["module"]["wfm_compose"]["extra_methods"] = [dict(r) for r in rows]
    C.save(root, cfg)


def _ext(root: Path) -> str:
    return (root / _EXT).read_text(encoding="utf-8")


class TestApplySeesTheAuthorsFile:
    """Cause 1: the replay tree never holds the author's file."""

    def test_a_file_with_no_row_is_still_included(self, tmp_path):
        # No row declares anything, so only the file's presence -- in the
        # REAL project -- can put the include there.
        _project(tmp_path)
        apply_run(tmp_path)
        assert _INCLUDE not in _ext(tmp_path)
        (tmp_path / _EXTRA).write_text("/* helpers */\n", encoding="utf-8")
        apply_run(tmp_path)
        assert _INCLUDE in _ext(tmp_path)

    def test_a_declared_row_is_included_before_the_file_exists(self, tmp_path):
        _project(tmp_path)
        _declare(tmp_path, {"name": "draws", "fn": "Composer_draws"})
        apply_run(tmp_path)
        assert _INCLUDE in _ext(tmp_path)

    def test_writing_the_file_after_apply_changes_nothing(self, tmp_path):
        """The binding must not depend on whether the author wrote the file
        before or after the `apply` that declared its row."""
        _project(tmp_path)
        _declare(tmp_path, {"name": "draws", "fn": "Composer_draws"})
        apply_run(tmp_path)
        before = _ext(tmp_path)
        (tmp_path / _EXTRA).write_text("/* body */\n", encoding="utf-8")
        apply_run(tmp_path)
        assert _ext(tmp_path) == before


class TestTheRowHasADeclarationItCanSee:
    """Cause 2: the table precedes the include, so the row needs a
    prototype that precedes the table."""

    def test_the_prototype_precedes_the_row(self, tmp_path):
        _project(tmp_path)
        _declare(tmp_path, {"name": "draws", "fn": "Composer_draws"})
        apply_run(tmp_path)
        ext = _ext(tmp_path)
        proto = "static PyObject *Composer_draws(PyObject *, PyObject *);"
        row = "(PyCFunction)(void (*)(void))Composer_draws"
        assert proto in ext, ext
        assert ext.index(proto) < ext.index(row) < ext.index(_INCLUDE)

    def test_each_fn_is_declared_once(self, tmp_path):
        # Two rows may share one C function (on two types, say); a second
        # identical prototype is legal C but noise.
        _project(tmp_path)
        row = {"name": "draws", "fn": "Composer_draws"}
        _declare(tmp_path, row, {**row, "type": "Synth"})
        apply_run(tmp_path)
        assert _ext(tmp_path).count("*Composer_draws(PyObject") == 1

    def test_no_rows_no_prototypes(self, tmp_path):
        _project(tmp_path)
        apply_run(tmp_path)
        assert "extra_methods (gh-1190)" not in _ext(tmp_path)


@pytest.mark.parametrize(
    "flags, params",
    [
        ("METH_NOARGS", "PyObject *, PyObject *"),
        ("METH_O", "PyObject *, PyObject *"),
        ("METH_VARARGS", "PyObject *, PyObject *"),
        ("METH_VARARGS | METH_KEYWORDS", "PyObject *, PyObject *, PyObject *"),
        ("METH_NOARGS | METH_CLASS", "PyObject *, PyObject *"),
        ("METH_FASTCALL", "PyObject *, PyObject *const *, Py_ssize_t"),
        (
            "METH_FASTCALL | METH_KEYWORDS",
            "PyObject *, PyObject *const *, Py_ssize_t, PyObject *",
        ),
        (
            "METH_METHOD | METH_FASTCALL | METH_KEYWORDS",
            "PyObject *, PyTypeObject *, PyObject *const *, Py_ssize_t,"
            " PyObject *",
        ),
    ],
)
def test_the_signature_follows_the_flags(flags: str, params: str) -> None:
    """CPython's calling conventions, as `PyMethodDef` documents them."""
    assert _composer._extra_method_params(flags) == params
