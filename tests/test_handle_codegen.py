"""Codegen tests for ``kind = "handle"`` modules (gh-306).

Render the ``<module>_ext.c`` / CMakeLists / .pyi for a handle module and
assert the generated text has the right shape — the typed ``PyTypeObject``, the
opaque-create ``tp_init``, the decoded-getter property (the genuinely-new C),
the context-manager / close / dealloc RAII protocol, the weak-symbol backend
guard, and the CMake link line — without needing a C compiler.

Two examples exercise the generator: a wfm-like ``Writer`` (the doppler
archetype) AND a deliberately **non-wfm** ``Ring`` over a toy ``ringbuf_t``
(the genericity gate — a derived-``expr`` ``stats`` getter, int-in→array-out
``pop``, array-in→scalar ``push``, no wfm coupling)."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle


# ── the wfm-like Writer archetype ────────────────────────────────────────────


def _writer_cfg(optional_backend=None):
    mod = {
        "kind": "handle",
        "backing": "wfm_writer",
        "package": "wfm",
        "header": "wfm/wfm_writer.h",
        "type_name": "Writer",
        "context_manager": True,
        "close_fn": "wfm_writer_close",
        "create_fn": "wfm_writer_open",
        "depends_on": [{"name": "wfm_writer", "link": True}],
        "extra_link_libs": ["m"],
        "create_args": [
            {"name": "path", "type": "path"},
            {
                "name": "file_type",
                "type": "int",
                "enum": "ftype",
                "default": "raw",
                "kwonly": True,
            },
            {
                "name": "sample_type",
                "type": "int",
                "enum": "stype",
                "default": "cf32",
                "kwonly": True,
            },
            {"name": "headroom", "type": "double", "default": "0.0"},
        ],
        "create_post": [
            {
                "fn": "wfm_writer_set_gain",
                "when": "headroom",
                "arg": "pow(10, -headroom/20)",
            }
        ],
        "methods": [
            {
                "name": "write",
                "fn": "wfm_writer_write",
                "returns": "size_t",
                "nogil": True,
                "args": [{"name": "iq", "type": "float _Complex[]"}],
            }
        ],
        "getters": [
            {
                "fn": "wfm_writer_stats",
                "out": "wfm_writer_stats_t",
                "cache": False,
                "fields": [
                    {
                        "name": "clip_fraction",
                        "from": "frac",
                        "type": "double",
                    },
                    {
                        "name": "peak_dbfs",
                        "type": "double",
                        "expr": "tmp.peak > 0 ? 20*log10(tmp.peak) : -INFINITY",
                    },
                    {
                        "name": "clipped",
                        "type": "bool",
                        "expr": "self->sample_type >= 2 && tmp.peak > 1.0",
                    },
                ],
            }
        ],
    }
    if optional_backend:
        mod["optional_backend"] = optional_backend
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [
            {"name": "ftype", "values": ["raw", "csv"]},
            {"name": "stype", "values": ["cf32", "cf64", "ci16"]},
        ],
        "module": {"wfm_writer": mod},
    }


def _wsrc(**kw):
    return _handle.render_ext(_writer_cfg(**kw), "wfm_writer")


# ── the NON-wfm Ring genericity gate ─────────────────────────────────────────
#
# A toy ringbuf_t { push(samples) -> dropped, pop(n) -> samples, stats(&out) }.
# No wfm symbols; a `stats` getter carries a *derived expr* (fill_fraction =
# used/capacity), proving the generator is not wfm-coupled.


def _ring_cfg():
    mod = {
        "kind": "handle",
        "backing": "ringbuf",
        "type_name": "Ring",
        "context_manager": True,
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        "create_args": [{"name": "capacity", "type": "size_t"}],
        "methods": [
            {
                "name": "push",
                "fn": "ringbuf_push",
                "returns": "size_t",
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                "name": "pop",
                "fn": "ringbuf_pop",
                "returns": "float[]",
                "args": [{"name": "n", "type": "size_t"}],
            },
            {"name": "clear", "fn": "ringbuf_clear"},
        ],
        "getters": [
            {
                "fn": "ringbuf_stats",
                "out": "ringbuf_stats_t",
                "cache": False,
                "fields": [
                    {"name": "used", "type": "size_t"},
                    {
                        "name": "fill_fraction",
                        "type": "double",
                        "expr": "self->capacity ? "
                        "(double)tmp.used / (double)self->capacity : 0.0",
                    },
                ],
            }
        ],
    }
    return {
        "project": {"name": "toybox", "version": "0.1.0"},
        "module": {"ringbuf": mod},
    }


def _rsrc():
    return _handle.render_ext(_ring_cfg(), "ringbuf")


# ── tests ─────────────────────────────────────────────────────────────────────


class TestTypeAndInit:
    def test_pytypeobject_present(self):
        s = _wsrc()
        assert "static PyTypeObject WriterType = {" in s
        assert "typedef struct {" in s and "wfm_writer_t *h;" in s
        assert "int       closed;" in s
        assert ".tp_init      = (initproc)Writer_init," in s

    def test_tp_init_calls_create_fn(self):
        s = _wsrc()
        assert "Writer_init(WriterObject *self" in s
        assert "self->h = wfm_writer_open(" in s
        assert (
            'PyErr_SetString(PyExc_RuntimeError, "wfm_writer_open failed");'
            in s
        )

    def test_path_coercion_and_uaf_safety(self):
        s = _wsrc()
        # os.fspath coercion via PyUnicode_FSConverter (O& form).
        assert "PyUnicode_FSConverter, &path" in s
        # gh-219: the borrowed path bytes are DECREF'd only AFTER create_fn
        # has copied them — the decref must appear below the create_fn call.
        assert "PyBytes_AS_STRING(path)" in s
        ci = s.index("self->h = wfm_writer_open(")
        di = s.index("Py_XDECREF(path);", ci)
        assert di > ci

    def test_enum_args_validated_to_index(self):
        s = _wsrc()
        # Reuses the composer SSOT _enum_index lookup + per-enum tables.
        assert "_enum_index(const char *const *tab" in s
        assert "static const char *const _enum_ftype[]" in s
        assert "int _arg_file_type = _enum_index(_enum_ftype, file_type);" in s

    def test_create_post_setter_guarded(self):
        s = _wsrc()
        assert "if (headroom)" in s
        assert "wfm_writer_set_gain(self->h, pow(10, -headroom/20));" in s

    def test_stashed_init_for_expr(self):
        s = _wsrc()
        # `clipped` expr references self->sample_type → stashed in tp_init.
        assert "int sample_type;" in s  # struct field
        assert "self->sample_type = _arg_sample_type;" in s  # stash assign


class TestMethods:
    def test_array_in_scalar_return(self):
        s = _wsrc()
        assert "Writer_write(WriterObject *self, PyObject *args)" in s
        assert "PyArray_FROM_OTF(\n        x_obj, NPY_COMPLEX64" in s
        assert "r = wfm_writer_write(self->h, in_data, n_in);" in s
        # size_t return -> py int (via _CTYPE_META, reused).
        assert "PyLong_FromUnsignedLongLong((unsigned long long)r)" in s
        # nogil releases the GIL across the kernel.
        assert "Py_BEGIN_ALLOW_THREADS" in s

    def test_scalar_void_method(self):
        s = _rsrc()
        assert "Ring_clear(RingObject *self, PyObject *args)" in s
        assert "ringbuf_clear(self->h);" in s
        assert "Py_RETURN_NONE;" in s

    def test_int_in_array_out_returns_owned_array(self):
        s = _rsrc()
        assert "Ring_pop(RingObject *self, PyObject *args)" in s
        # allocates an independent numpy-owned array, never a dangling view.
        assert "PyArray_SimpleNew(1, dims, NPY_FLOAT)" in s
        assert "got = ringbuf_pop(self->h, out, (size_t)n);" in s
        assert "PyArray_DIMS((PyArrayObject *)arr)[0] = (npy_intp)got" in s
        # no zero-copy slice/view of a grow-on-demand buffer here.
        assert "PySlice_New" not in s


class TestDecodedGetters:
    def test_plain_and_aliased_field(self):
        s = _wsrc()
        assert "Writer_get_clip_fraction(WriterObject *self" in s
        assert "wfm_writer_stats(self->h, &tmp);" in s
        assert "PyFloat_FromDouble(tmp.frac)" in s  # `from = frac`

    def test_expr_field_renders_verbatim(self):
        s = _wsrc()
        # the genuinely-new decoded-getter expr, wrapped to a PyObject.
        assert "tmp.peak > 0 ? 20*log10(tmp.peak) : -INFINITY" in s
        assert "self->sample_type >= 2 && tmp.peak > 1.0" in s
        assert '{"clipped", (getter)Writer_get_clipped, NULL, NULL, NULL}' in s

    def test_non_wfm_expr_getter(self):
        s = _rsrc()
        # genericity gate: derived expr over a non-wfm struct + stashed init.
        assert "ringbuf_stats(self->h, &tmp);" in s
        assert (
            "self->capacity ? (double)tmp.used / (double)self->capacity : 0.0"
            in s
        )
        assert "Ring_get_fill_fraction(RingObject *self" in s


class TestRAII:
    def test_context_manager_close_dealloc(self):
        s = _wsrc()
        assert "Writer_enter(WriterObject *self" in s
        assert "Writer_exit(WriterObject *self" in s
        assert "Writer_close(WriterObject *self" in s
        # idempotent close.
        assert "if (!self->closed && self->h) {" in s
        assert "wfm_writer_close(self->h);" in s
        assert "self->closed = 1;" in s
        # dealloc closes too.
        assert "Writer_dealloc(WriterObject *self)" in s
        assert ".tp_dealloc   = (destructor)Writer_dealloc," in s
        # method table wires the context-manager dunders.
        assert (
            '{"__enter__", (PyCFunction)Writer_enter, METH_NOARGS, NULL}' in s
        )
        assert (
            '{"__exit__", (PyCFunction)Writer_exit, METH_VARARGS, NULL}' in s
        )


class TestOptionalBackend:
    def test_weak_symbol_guard(self):
        s = _wsrc(optional_backend="wfm_zmq_sink_open")
        # weak extern declaration so a platform without it links with NULL.
        assert "__attribute__((weak))" in s
        assert "wfm_zmq_sink_open" in s
        # tp_init raises NotImplementedError when the symbol is NULL.
        assert "PyExc_NotImplementedError" in s

    def test_no_guard_when_unset(self):
        s = _wsrc()
        assert "__attribute__((weak))" not in s
        assert "PyExc_NotImplementedError" not in s


class TestModuleAndIncludes:
    def test_includes_and_header(self):
        s = _wsrc()
        assert "#include <Python.h>" in s
        assert "#include <numpy/arrayobject.h>" in s
        assert '#include "wfm/wfm_writer.h"' in s
        assert "#include <math.h>" in s  # log10 etc. for exprs

    def test_module_init_registers_type(self):
        s = _wsrc()
        assert 'PyModuleDef_HEAD_INIT, "wfm_writer"' in s
        assert "PyMODINIT_FUNC\nPyInit_wfm_writer(void)" in s
        assert "PyType_Ready(&WriterType)" in s
        assert 'PyModule_AddObject(m, "Writer"' in s


class TestCMake:
    def test_link_line(self):
        cm = _handle.render_cmake(_writer_cfg(), "wfm_writer")
        assert "Python3_add_library(wfm_writer MODULE WITH_SOABI" in cm
        assert "wfm_writer_core" in cm  # link = true dep core
        assert "    m\n" in cm  # extra_link_libs
        assert "Python3::NumPy" in cm
        # package override → drops into the wfm package dir.
        assert "PYTHON_PACKAGE_DIR}/wfm" in cm


class TestPyi:
    def test_class_stub(self):
        pyi = _handle.render_pyi(_writer_cfg(), "wfm_writer")
        assert "class Writer:" in pyi
        assert "def __init__(self, path: str" in pyi
        assert "def write(self, x: NDArray[Any]) -> int: ..." in pyi
        assert "@property" in pyi
        assert "def clip_fraction(self) -> float: ..." in pyi
        assert "def __enter__(self) -> Writer: ..." in pyi
        assert "def close(self) -> None: ..." in pyi

    def test_non_wfm_class_stub(self):
        pyi = _handle.render_pyi(_ring_cfg(), "ringbuf")
        assert "class Ring:" in pyi
        assert "def pop(self, n: int) -> NDArray[Any]: ..." in pyi
        assert "def fill_fraction(self) -> float: ..." in pyi


class TestWellFormedC:
    """Guards the bug class a text-assertion misses but the compiler hits: a
    stray brace in the generated method table. The gh-306 end-to-end build
    caught `{"close", ...}},` (a doubled brace) — these keep it caught here."""

    def test_method_table_braces_balanced(self):
        for src in (_wsrc(), _rsrc()):
            assert src.count("{") == src.count("}"), "unbalanced braces"
            # flat method rows + designated-initializer structs never emit a
            # doubled `}}`; one is the close-row codegen bug.
            assert "}}" not in src, "doubled brace in generated C"

    def test_close_row_well_formed(self):
        assert (
            '{"close", (PyCFunction)Ring_close, METH_NOARGS, '
            '"close() -> None"},'
        ) in _rsrc()


class TestMixedArgMethod:
    """#308: an array arg + trailing scalars must parse and pass through to the
    C fn, not silently drop the scalars — the ZmqSink.send(iq, fs, fc) shape."""

    def test_array_plus_scalars(self):
        m = {
            "name": "send",
            "fn": "wfm_zmq_sink_send",
            "args": [
                {"name": "iq", "type": "float _Complex[]"},
                {"name": "fs", "type": "double"},
                {"name": "fc", "type": "double"},
            ],
        }
        s = _handle._emit_method(_writer_cfg(), "wfm_writer", m)
        assert 'PyArg_ParseTuple(args, "Odd", &x_obj, &fs, &fc)' in s
        assert "double fs;" in s and "double fc;" in s
        assert "wfm_zmq_sink_send(self->h, in_data, n_in, fs, fc)" in s

    def test_more_than_one_array_arg_is_unsupported(self):
        m = {
            "name": "bad",
            "fn": "f",
            "args": [
                {"name": "a", "type": "float[]"},
                {"name": "b", "type": "float[]"},
            ],
        }
        with pytest.raises(NotImplementedError):
            _handle._emit_method(_writer_cfg(), "wfm_writer", m)
