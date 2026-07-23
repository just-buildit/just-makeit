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
            {
                # #311 shape (d): array-in + writable array-out.
                "name": "scale",
                "fn": "ringbuf_scale",
                "returns": "float[]",
                "nogil": True,
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": "out", "type": "float[]", "writable": True},
                ],
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
            },
            {
                # #311 writable scalar property (scalar return-by-value getter).
                "fn": "ringbuf_get_gain",
                "out": "float",
                "fields": [
                    {
                        "name": "gain",
                        "type": "float",
                        "writable_fn": "ringbuf_set_gain",
                    },
                ],
            },
        ],
    }
    return {
        "project": {"name": "toybox", "version": "0.1.0"},
        "module": {"ringbuf": mod},
    }


def _rsrc():
    return _handle.render_ext(_ring_cfg(), "ringbuf")


def _save_cfg(save_method=None):
    """A Ring handle plus a gh-565 `save` method returning `bytes`, sized by an
    `out_len_fn`. Pass *save_method* to override the method dict (e.g. to omit
    out_len_fn for the validation test, or add a scalar arg)."""
    cfg = _ring_cfg()
    m = save_method or {
        "name": "save",
        "fn": "ringbuf_save",
        "out_len_fn": "ringbuf_save_bytes",
        "returns": "bytes",
    }
    cfg["module"]["ringbuf"]["methods"].append(m)
    return cfg


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

    def test_no_default_arg_is_required(self):
        # gh-178 review #6: a create-arg with no `default` parses as REQUIRED —
        # the `|` separates it from the defaulted args, not before everything
        # (which let ZmqSink() build with a NULL endpoint and crash).
        s = _wsrc()
        # path (no default) is required; the defaulted args follow the `|`.
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "O&|ssd", kwlist,' in s

    def test_reinit_releases_prior_handle(self):
        # gh-178 review #9: a second __init__() must tear down the live handle
        # before overwriting it, else it leaks. The teardown precedes the
        # create_fn call and guards on the (closed, h) state.
        s = _wsrc()
        teardown = s.index("if (!self->closed && self->h) {")
        create = s.index("self->h = wfm_writer_open(")
        assert teardown < create
        # the teardown closes and re-marks closed so create_fn can re-init.
        seg = s[teardown:create]
        assert "wfm_writer_close(self->h);" in seg
        assert "self->h = NULL;" in seg


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
        # no zero-copy slice/view *in pop* (the grow-on-demand shape returns an
        # owned array; the caller-buffer execute shape (d) legitimately does).
        i = s.index("Ring_pop(RingObject")
        pop_fn = s[i : s.index("\nstatic ", i)]
        assert "PySlice_New" not in pop_fn


class TestBytesReturn:
    """gh-565 shape (f): scalar/string args -> HANDLE-length `bytes`."""

    def test_bytes_out_packs_pybytes(self):
        s = _handle.render_ext(_save_cfg(), "ringbuf")
        assert "Ring_save(RingObject *self, PyObject *args)" in s
        # size from the handle, temp buffer, fill, COPY into immutable bytes,
        # free the temp — no aliasing, no numpy machinery.
        assert "size_t _n = (size_t)ringbuf_save_bytes(self->h);" in s
        assert "char *_buf = (char *)PyMem_Malloc(_n ? _n : 1);" in s
        assert "if (!_buf) return PyErr_NoMemory();" in s
        assert "_got = ringbuf_save(self->h, _buf);" in s
        assert (
            "PyObject *_r = PyBytes_FromStringAndSize(_buf,"
            " (Py_ssize_t)_got);" in s
        )
        assert "PyMem_Free(_buf);" in s
        assert "PyArray_SimpleNew" not in s[s.index("Ring_save") :]

    def test_bytes_out_pyi_is_bytes(self):
        pyi = _handle.render_pyi(_save_cfg(), "ringbuf")
        assert "def save(self) -> bytes:" in pyi

    def test_bytes_out_with_scalar_arg(self):
        # A scalar arg parses positionally and passes through before the buffer.
        cfg = _save_cfg(
            {
                "name": "save",
                "fn": "ringbuf_save",
                "out_len_fn": "ringbuf_save_bytes",
                "returns": "bytes",
                "args": [{"name": "level", "type": "int"}],
            }
        )
        s = _handle.render_ext(cfg, "ringbuf")
        assert "_got = ringbuf_save(self->h, level_raw, _buf);" in s
        pyi = _handle.render_pyi(cfg, "ringbuf")
        assert "def save(self, level: int) -> bytes:" in pyi

    def test_bytes_out_requires_out_len_fn(self):
        bad = {"name": "save", "fn": "ringbuf_save", "returns": "bytes"}
        with pytest.raises(ValueError, match="requires an 'out_len_fn'"):
            _handle.render_ext(_save_cfg(bad), "ringbuf")

    def test_bytes_method_is_positional(self):
        assert (
            _handle._method_kwargs(
                {
                    "name": "save",
                    "returns": "bytes",
                    "args": [{"name": "l", "type": "int"}],
                }
            )
            is False
        )


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


class TestCloseStatus:
    """gh-178 review #5: when close_fn reports a status code, close() captures
    it and raises on a non-zero result; tp_dealloc stays silent (can't raise)."""

    def test_close_raises_on_nonzero_rc(self):
        cfg = _writer_cfg()
        cfg["module"]["wfm_writer"]["close_returns"] = "int"
        s = _handle.render_ext(cfg, "wfm_writer")
        # close() captures the rc and raises RuntimeError on non-zero.
        assert "int _rc = wfm_writer_close(self->h);" in s
        assert "PyErr_Format(PyExc_RuntimeError,\n" in s
        assert '"wfm_writer_close failed (rc=%d)", (int)_rc);' in s
        # the handle is torn down + marked closed before raising (one-shot).
        ci = s.index("Writer_close(WriterObject *self")
        seg = s[ci : s.index("\nstatic ", ci)]
        assert "self->closed = 1;" in seg
        assert "return NULL;" in seg

    def test_dealloc_stays_silent(self):
        cfg = _writer_cfg()
        cfg["module"]["wfm_writer"]["close_returns"] = "int"
        s = _handle.render_ext(cfg, "wfm_writer")
        di = s.index("Writer_dealloc(WriterObject *self)")
        seg = s[
            di : s.index("\nstatic ", di) if "\nstatic " in s[di:] else len(s)
        ]
        # dealloc never captures the rc or raises — it just calls close_fn.
        assert "wfm_writer_close(self->h);" in seg
        assert "PyErr_Format" not in seg

    def test_void_close_unchanged(self):
        # default (no close_returns): the silent close stays as before.
        s = _wsrc()
        assert "int _rc =" not in s
        ci = s.index("Writer_close(WriterObject *self")
        seg = s[ci : s.index("\nstatic ", ci)]
        assert "wfm_writer_close(self->h);" in seg
        assert "PyErr_Format" not in seg


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
        # Methods, properties and RAII now have docstrings (gh-374).
        assert "def write(self, x: NDArray[Any]) -> int:" in pyi
        assert '"""write(x) -> int."""' in pyi
        assert "@property" in pyi
        assert "def clip_fraction(self) -> float:" in pyi
        assert "def __enter__(self) -> Writer:" in pyi
        assert '"""Enter context; return self."""' in pyi
        assert "def close(self) -> None:" in pyi
        assert '"""Release the handle and free resources."""' in pyi

    def test_non_wfm_class_stub(self):
        pyi = _handle.render_pyi(_ring_cfg(), "ringbuf")
        assert "class Ring:" in pyi
        assert "def pop(self, n: int) -> NDArray[Any]:" in pyi
        assert '"""pop(n) -> NDArray[Any]."""' in pyi
        assert "def fill_fraction(self) -> float:" in pyi
        assert '"""fill_fraction (float)."""' in pyi

    def test_class_docstring_parameters(self):
        """gh-374: class docstring exposes defaults and enum choices."""
        pyi = _handle.render_pyi(_writer_cfg(), "wfm_writer")
        # Class docstring Parameters block is present.
        assert "Parameters" in pyi
        assert "----------" in pyi
        # Enum args show their default and choices.
        assert 'file_type : str, default ``"raw"``' in pyi
        assert '``"raw"``, ``"csv"``' in pyi
        assert 'sample_type : str, default ``"cf32"``' in pyi
        assert '``"cf32"``, ``"cf64"``, ``"ci16"``' in pyi
        # Numeric defaults are bare (no quotes).
        assert "headroom : float, default 0.0" in pyi

    def test_class_docstring_no_args(self):
        """A handle with no create_args gets a single-line class docstring."""
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "module": {
                "tok": {
                    "kind": "handle",
                    "backing": "tok",
                    "type_name": "Tok",
                    "create_fn": "tok_open",
                }
            },
        }
        pyi = _handle.render_pyi(cfg, "tok")
        assert '"""Tok handle."""' in pyi

    def test_property_enum_choices(self):
        """gh-374: enum getter fields show choices in their docstring."""
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "enum": [{"name": "ftype", "values": ["raw", "csv", "blue"]}],
            "module": {
                "rdr": {
                    "kind": "handle",
                    "backing": "rdr",
                    "type_name": "Reader",
                    "create_fn": "rdr_open",
                    "create_args": [{"name": "path", "type": "path"}],
                    "getters": [
                        {
                            "fn": "rdr_info",
                            "out": "rdr_info_t",
                            "fields": [
                                {
                                    "name": "file_type",
                                    "type": "int",
                                    "enum": "ftype",
                                },
                                {"name": "fs", "type": "double"},
                            ],
                        }
                    ],
                }
            },
        }
        pyi = _handle.render_pyi(cfg, "rdr")
        assert (
            '"""file_type (str); one of ``"raw"``, ``"csv"``, ``"blue"``."""'
            in pyi
        )
        assert '"""fs (float)."""' in pyi

    def test_method_scalar_default_in_docstring(self):
        """gh-374: scalar method args with defaults are named in the doc call."""
        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "module": {
                "sink": {
                    "kind": "handle",
                    "backing": "sink",
                    "type_name": "Sink",
                    "create_fn": "sink_open",
                    "create_args": [{"name": "addr", "type": "path"}],
                    "methods": [
                        {
                            "name": "send",
                            "fn": "sink_send",
                            "returns": "size_t",
                            "args": [
                                {"name": "x", "type": "float _Complex[]"},
                                {"name": "fs", "type": "double"},
                                {
                                    "name": "fc",
                                    "type": "double",
                                    "default": "0.0",
                                },
                            ],
                        }
                    ],
                }
            },
        }
        pyi = _handle.render_pyi(cfg, "sink")
        assert '"""send(x, fs, fc=0.0) -> int."""' in pyi


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
        # Trailing scalars parse with keywords (gh-178 review #6) so they can
        # carry defaults; the array name leads the kwlist.
        assert (
            "Writer_send(WriterObject *self, PyObject *args, PyObject *kwds)"
            in s
        )
        assert 'static char *kwlist[] = {"iq", "fs", "fc", NULL};' in s
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "Odd", kwlist,' in s
        assert "&x_obj, &fs, &fc" in s
        assert "double fs;" in s and "double fc;" in s
        assert "wfm_zmq_sink_send(self->h, in_data, n_in, fs, fc)" in s

    def test_array_plus_scalar_default(self):
        """gh-178 review #6: a trailing scalar `default` inserts the `|` split
        and shows as `= ...` in the stub — the hand-written send had an fc
        default the old positional parse dropped."""
        m = {
            "name": "send",
            "fn": "wfm_zmq_sink_send",
            "args": [
                {"name": "iq", "type": "float _Complex[]"},
                {"name": "fs", "type": "double"},
                {"name": "fc", "type": "double", "default": "0.0"},
            ],
        }
        s = _handle._emit_method(_writer_cfg(), "wfm_writer", m)
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "Od|d", kwlist,' in s
        assert "double fc = 0.0;" in s

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


class TestExecuteShape:
    """#311 shape (d): array-in + writable array-out -> out[:n_out] view."""

    def test_caller_buffer_execute(self):
        m = {
            "name": "execute",
            "fn": "ddcr_execute",
            "returns": "float _Complex[]",
            "nogil": True,
            "args": [
                {"name": "x", "type": "float[]"},
                {"name": "out", "type": "float _Complex[]", "writable": True},
            ],
        }
        s = _handle._emit_method(_writer_cfg(), "wfm_writer", m)
        # both arrays parse; input is marshaled, output is validated not cast.
        assert 'PyArg_ParseTuple(args, "OO", &x_obj, &out_obj)' in s
        assert "must be a writable ndarray of the output dtype" in s
        assert "NPY_ARRAY_WRITEABLE" in s
        # exact call into the caller buffer, under nogil, returning the view.
        assert "ddcr_execute(self->h, in_data, n_in, out_data, max_out)" in s
        assert "Py_BEGIN_ALLOW_THREADS" in s
        assert "PySlice_New(NULL, stop, NULL)" in s  # out[:n_out]

    def test_writable_out_in_ring_cfg_compiles_text(self):
        s = _rsrc()
        assert "Ring_scale(RingObject *self" in s
        assert "ringbuf_scale(self->h, in_data, n_in, out_data, max_out)" in s


class TestWritableProperty:
    """#311 writable scalar property: a scalar return-by-value getter whose
    field names a setter emits the (setter) slot + a PyArg_Parse coercion."""

    def test_scalar_getter_and_setter_slot(self):
        s = _rsrc()
        # scalar getter: return-by-value, not an out-pointer fill.
        assert "tmp = ringbuf_get_gain(self->h);" in s
        assert "PyFloat_FromDouble((double)tmp)" in s
        # the setter coerces the scalar and calls set_fn.
        assert "Ring_set_gain(RingObject *self, PyObject *value" in s
        assert 'PyArg_Parse(value, "f", &v)' in s
        assert "ringbuf_set_gain(self->h, v);" in s
        # getset row wires both slots.
        assert (
            '{"gain", (getter)Ring_get_gain, '
            "(setter)Ring_set_gain, NULL, NULL}" in s
        )

    def test_pyi_exposes_setter(self):
        pyi = _handle.render_pyi(_ring_cfg(), "ringbuf")
        assert "@gain.setter" in pyi
        # Setter stays as a one-liner; getter now has a docstring (gh-374).
        assert "def gain(self, value: float) -> None: ..." in pyi
        assert "def scale(self, x: NDArray[Any], out: NDArray[Any])" in pyi


def _handle_cfg(mod):
    return {"project": {"name": "p", "version": "0.1.0"}, "module": {"m": mod}}


class TestInitInPlace:
    """#315: init_fn over a caller-allocated struct — jm mallocs + inits + frees
    (vs create_fn, which allocates and returns the handle)."""

    def _cfg(self, close_fn=None):
        mod = {
            "kind": "handle",
            "type_name": "Clock",
            "handle_type": "dp_sample_clock_t",
            "init_fn": "dp_sample_clock_init",
            "create_args": [
                {"name": "fs", "type": "double"},
                {"name": "resync", "type": "int", "default": "0"},
            ],
        }
        if close_fn:
            mod["close_fn"] = close_fn
        return _handle_cfg(mod)

    def test_tp_init_mallocs_and_inits(self):
        s = _handle.render_ext(self._cfg(), "m")
        assert (
            "self->h = (dp_sample_clock_t *)malloc(sizeof(dp_sample_clock_t));"
            in s
        )
        assert "PyErr_NoMemory();" in s
        assert "dp_sample_clock_init(self->h, fs, resync);" in s
        # init_fn returns void — no create_fn "failed" path.
        assert "failed" not in s

    def test_close_and_dealloc_free(self):
        s = _handle.render_ext(self._cfg(), "m")
        # jm owns the malloc: both close and dealloc free it.
        assert s.count("free(self->h);") >= 2
        assert "<stdlib.h>" in s  # malloc/free available

    def test_close_fn_finalizes_before_free(self):
        s = _handle.render_ext(self._cfg(close_fn="dp_sample_clock_fini"), "m")
        # a declared close_fn finalizes owned members, then jm frees the struct.
        assert "dp_sample_clock_fini(self->h); free(self->h);" in s


class TestPerFieldGetter:
    """#314: each field names its own scalar getter `T fn(h)` — no struct shim."""

    def _cfg(self):
        return _handle_cfg(
            {
                "kind": "handle",
                "backing": "wr",
                "type_name": "W",
                "create_fn": "wr_open",
                "close_fn": "wr_close",
                "getters": [
                    {
                        # no `fn`/`out` — per-field scalar getters.
                        "fields": [
                            {
                                "name": "clip_fraction",
                                "getter": "wr_clip_fraction",
                                "type": "double",
                            },
                            {
                                "name": "peak_dbfs",
                                "getter": "wr_peak",
                                "type": "double",
                                "returns": "double",  # gh-333: explicit
                                "expr": "tmp > 0 ? 20*log10(tmp) : -INFINITY",
                            },
                        ],
                    }
                ],
            }
        )

    def test_per_field_scalar_getters(self):
        s = _handle.render_ext(self._cfg(), "m")
        assert "tmp = wr_clip_fraction(self->h);" in s
        assert "tmp = wr_peak(self->h);" in s
        assert "W_get_clip_fraction(WObject *self" in s
        assert "W_get_peak_dbfs(WObject *self" in s
        # expr references the scalar as `tmp` (standardized w/ the scalar-out
        # path), not the issue's draft `x`.
        assert "tmp > 0 ? 20*log10(tmp) : -INFINITY" in s
        # no struct out-pointer fill (the shim this removes).
        assert ", &tmp)" not in s

    def test_per_field_expr_without_returns_raises(self):
        # gh-333: a per-field getter with an `expr` but no `returns` is the
        # silent-truncation trap (the getter's value is cast to the field type
        # BEFORE the expr). Require `returns` rather than guess.
        cfg = _handle_cfg(
            {
                "kind": "handle",
                "backing": "wr",
                "type_name": "W",
                "create_fn": "wr_open",
                "close_fn": "wr_close",
                "getters": [
                    {
                        "fields": [
                            {
                                "name": "clipped",
                                "getter": "wr_peak",
                                "type": "bool",
                                "expr": "tmp > 1.0",  # no `returns`
                            }
                        ]
                    }
                ],
            }
        )
        with pytest.raises(ValueError, match="returns"):
            _handle.render_ext(cfg, "m")

    def test_per_field_plain_getter_no_returns_ok(self):
        # a plain per-field getter (no expr) needs no `returns` — the getter
        # returns the field type directly.
        cfg = _handle_cfg(
            {
                "kind": "handle",
                "backing": "wr",
                "type_name": "W",
                "create_fn": "wr_open",
                "close_fn": "wr_close",
                "getters": [
                    {
                        "fields": [
                            {
                                "name": "rate",
                                "getter": "wr_rate",
                                "type": "double",
                            }
                        ]
                    }
                ],
            }
        )
        s = _handle.render_ext(cfg, "m")
        assert "double tmp;" in s and "tmp = wr_rate(self->h);" in s

    def test_tmp_typed_by_getter_return_not_field_type(self):
        # gh-326: `tmp` is the GETTER's return type (`returns`), not the field's
        # decoded type — a bool `clipped` derived from a double peak keeps full
        # precision; `<stdbool.h>` is included so `bool` compiles.
        cfg = _handle_cfg(
            {
                "kind": "handle",
                "backing": "wr",
                "type_name": "W",
                "create_fn": "wr_open",
                "close_fn": "wr_close",
                "getters": [
                    {
                        "fields": [
                            {
                                "name": "clipped",
                                "getter": "wr_peak",
                                "type": "bool",
                                "returns": "double",
                                "expr": "tmp > 1.0",
                            }
                        ]
                    }
                ],
            }
        )
        s = _handle.render_ext(cfg, "m")
        assert "    double tmp;" in s  # getter return type, not `bool tmp;`
        assert "bool tmp;" not in s
        assert "tmp = wr_peak(self->h);" in s
        assert "PyBool_FromLong((long)(tmp > 1.0))" in s  # decoded to bool
        assert "#include <stdbool.h>" in s


class TestMethodKwargs:
    """#319: scalar method args honor default + keyword passing."""

    def _cfg(self):
        return _handle_cfg(
            {
                "kind": "handle",
                "backing": "w",
                "type_name": "W",
                "create_fn": "w_open",
                "close_fn": "w_close",
                "methods": [
                    {
                        "name": "track_clipping",
                        "fn": "w_track_clipping",
                        "args": [
                            {"name": "on", "type": "int", "default": "1"}
                        ],
                    }
                ],
            }
        )

    def test_keyword_and_default_parse(self):
        s = _handle.render_ext(self._cfg(), "m")
        assert (
            "W_track_clipping(WObject *self, PyObject *args, PyObject *kwds)"
            in s
        )
        assert 'static char *kwlist[] = {"on", NULL};' in s
        assert "int on = 1;" in s  # default
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "|i", kwlist,' in s
        # the method table registers keywords so on= works.
        assert (
            '{"track_clipping", (PyCFunction)W_track_clipping, '
            "METH_VARARGS | METH_KEYWORDS, NULL}" in s
        )


# ── string create-arg + handle-length array-out (gh: wfm_plan feature) ────────
#
# A Plan-like handle: constructed from a JSON spec STRING, exposing render(json)
# and a scalar fast-path at(snr, seed), both returning a cf32 array whose length
# comes from the handle via out_len_fn (not from an arg). Exercises F1 (a
# `string` create/method arg) and F2 (the `out_len_fn` array-out method shape).


def _plan_cfg():
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "module": {
            "wfm_plan": {
                "kind": "handle",
                "backing": "wfm_plan",
                "package": "wfm",
                "header": "wfm/wfm_plan.h",
                "type_name": "Plan",
                "context_manager": True,
                "close_fn": "wfm_plan_destroy",
                "create_fn": "wfm_plan_prepare",
                "create_args": [{"name": "spec_json", "type": "string"}],
                "methods": [
                    {
                        "name": "render",
                        "fn": "wfm_plan_render",
                        "returns": "float _Complex[]",
                        "out_len_fn": "wfm_plan_len",
                        "nogil": True,
                        "args": [{"name": "overrides_json", "type": "string"}],
                    },
                    {
                        "name": "at",
                        "fn": "wfm_plan_at",
                        "returns": "float _Complex[]",
                        "out_len_fn": "wfm_plan_len",
                        "nogil": True,
                        "args": [
                            {"name": "snr", "type": "double"},
                            {"name": "seed", "type": "uint64_t"},
                        ],
                    },
                ],
            }
        },
    }


class TestStringArgAndLenArray:
    def test_string_create_arg_parses_as_s(self):
        s = _handle.render_ext(_plan_cfg(), "wfm_plan")
        # F1: the JSON spec crosses as a borrowed const char * via "s".
        assert "const char *spec_json = NULL;" in s
        assert '"s"' in s  # the tp_init format string carries the string arg
        assert "wfm_plan_prepare(spec_json)" in s

    def test_render_string_in_len_out(self):
        m = _plan_cfg()["module"]["wfm_plan"]["methods"][0]
        s = _handle._emit_method(_plan_cfg(), "wfm_plan", m)
        # F2: string arg parsed, output sized from the handle, trimmed, nogil.
        assert 'PyArg_ParseTuple(args, "s", &overrides_json)' in s
        assert "PyArray_SimpleNew(1, &_n, NPY_COMPLEX64)" in s
        assert "wfm_plan_len(self->h)" in s
        assert "wfm_plan_render(self->h, overrides_json, _out)" in s
        assert "Py_BEGIN_ALLOW_THREADS" in s
        assert "/* trim */" in s

    def test_at_scalar_fast_path_safe_width(self):
        m = _plan_cfg()["module"]["wfm_plan"]["methods"][1]
        s = _handle._emit_method(_plan_cfg(), "wfm_plan", m)
        # safe-width parse: uint64_t via unsigned long long _raw + cast; "dK".
        assert 'PyArg_ParseTuple(args, "dK", &snr_raw, &seed_raw)' in s
        assert "unsigned long long seed_raw = 0;" in s
        assert "wfm_plan_at(self->h, snr_raw, (uint64_t)seed_raw, _out)" in s

    def test_len_out_method_is_positional(self):
        # shape (e) registers as plain METH_VARARGS (no keywords).
        s = _handle.render_ext(_plan_cfg(), "wfm_plan")
        assert '{"render", (PyCFunction)Plan_render, METH_VARARGS, NULL}' in s
        assert '{"at", (PyCFunction)Plan_at, METH_VARARGS, NULL}' in s

    def test_pyi_annotations(self):
        pyi = _handle.render_pyi(_plan_cfg(), "wfm_plan")
        assert "def __init__(self, spec_json: str) -> None: ..." in pyi
        assert "def render(self, overrides_json: str) -> NDArray[Any]:" in pyi
        assert "def at(self, snr: float, seed: int) -> NDArray[Any]:" in pyi
