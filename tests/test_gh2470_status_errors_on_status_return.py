"""A returned status and a rejected blob name their exception (gh-2470).

A method declared ``status_return = true`` raised ``ValueError("<name>
failed (rc=N)")`` for every non-zero status, and the generated ``set_state``
of a ``serializable`` object raised ``ValueError("set_state rejected the
blob")`` for every refusal. In doppler, ``Acquisition.set_carrier_freq_hz``
and ``Acquisition.set_state`` can return ``DP_ERR_MEMORY``, so an
out-of-memory failure reached Python as ``ValueError`` -- telling the caller
the argument was bad when it was not.

The answer is the table jm already has: ``status_errors`` rows (gh-1418 for
a borrow, gh-1614 for a ``check_return`` function), now also on

* an object (or view) method with ``status_return = true``,
* a ``kind = "handle"`` method whose ``int`` return is a status,
* the ``exit`` finalizer's ``__exit__``, which calls that method,
* the serializable ``set_state`` of an object or a handle, through the
  sibling key ``set_state_status_errors``.

Every face renders through `_borrow.status_switch_c` / `status_cases_c`,
with the old blanket exception as the fallback after the switch. With no
table the output is what it was. The end-to-end tests build real projects
and assert each exception's CLASS and MESSAGE.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jminc import INC_ROOT
from _jmrun import run_cli, script_round_trip

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _borrow  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._context._methods import set_state_check_c  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402

ROWS = [
    {"status": "P_ERR_MEMORY", "error": "MemoryError"},
    {"status": "P_ERR_INVALID", "error": "ValueError", "message": "rejected"},
]

#: The author's codes, declared in a header the binding includes. jm knows
#: none of them.
CODES = "enum { P_ERR_MEMORY = -2, P_ERR_INVALID = -4 };\n"


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _object_project(dest: Path, **object_kw) -> Path:
    """A standalone serializable `acq` with a `set_freq(hz)` status method
    and a `finish()` status method that `__exit__` calls."""
    _quiet(new_run, "p", dest, c_prefix=None)
    _quiet(
        object_run,
        dest,
        "acq",
        None,
        state_vars=[("n", "int", "0")],
        arg_type="float",
        return_type="float",
        serializable=True,
        **object_kw,
    )
    # `finish` first: `apply`'s replay resolves `exit` while it adds the
    # methods one at a time, so a finalizer declared later is not found yet.
    for name, params in (("finish", []), ("set_freq", [("hz", "int")])):
        _quiet(
            method_run,
            dest,
            "acq",
            name,
            None,
            "void",
            "int",
            False,
            [],
            params=params,
            status_return=True,
        )
    return dest


def _declare_tables(dest: Path) -> None:
    """Add every table by editing the manifest, as an author would."""
    cfg = C.load(dest)
    cfg["acq"]["set_state_status_errors"] = ROWS
    for m in cfg["acq"]["methods"]:
        m["status_errors"] = ROWS
    cfg["acq"]["destroy"] = {"exit": "finish"}
    C.save(dest, cfg)


def _ext(dest: Path) -> str:
    return (dest / "native/src/acq/acq_ext.c").read_text(encoding="utf-8")


def _pyi(dest: Path) -> str:
    return (dest / "src/p/acq.pyi").read_text(encoding="utf-8")


def _fn(text: str, head: str) -> str:
    """The C function whose definition starts with *head*, through `}`.

    The definition, not a forward declaration: its line opens the body.
    """
    m = re.search(rf"^{re.escape(head)}[^;\n]*\)\n\{{\n", text, re.M)
    assert m, head
    return text[m.start() : text.index("\n}", m.start()) + 2]


def _member_doc(pyi: str, name: str) -> str:
    i = pyi.index(f"    def {name}(")
    j = pyi.find("\n    def ", i + 1)
    return pyi[i : j if j != -1 else len(pyi)]


# -- the binding ---------------------------------------------------------------


def test_the_rows_switch_inside_the_status_test_before_the_blanket(tmp_path):
    dest = _object_project(tmp_path / "p")
    _declare_tables(dest)
    _quiet(apply_run, dest)
    ext = _ext(dest)
    for head, fallback in (
        ("Acq_set_freq(", '"set_freq failed"'),
        ("Acq_set_state(", '"set_state rejected the blob"'),
    ):
        body = _fn(ext, head)
        order = [
            body.index("if (_rc != 0) {"),
            body.index("switch (_rc) {"),
            body.index("case P_ERR_MEMORY:"),
            body.index("PyExc_MemoryError"),
            body.index("case P_ERR_INVALID:"),
            body.index('"rejected"'),
            body.index("default: break;"),
            body.index(fallback),
        ]
        assert order == sorted(order), (head, body)
    # The finalizer's rows reach `__exit__`, which calls it (gh-541).
    exit_body = _fn(ext, "Acq_exit(")
    assert "acq_finish(self->handle)" in exit_body
    assert "case P_ERR_MEMORY:" in exit_body


def test_one_emitter_for_every_table_face():
    """The method, set_state and function faces all emit `status_switch_c`'s
    text: a fix to the raise reaches each of them."""
    switch = _borrow.status_switch_c(ROWS, "set_state", {})
    assert switch in set_state_check_c("f(h, b)", ROWS)
    assert _borrow.status_cases_c(ROWS, "set_state", {}) in switch


# -- no table: the output is what it was ---------------------------------------

#: The pre-gh-2470 text of each block, verbatim. A project that declares no
#: table must render exactly this; a change here moves every existing
#: project's `_ext.c`.
_OLD_SET_STATE = (
    "    if (acq_set_state(self->handle, PyBytes_AS_STRING(arg)) != 0) {\n"
    '        PyErr_SetString(PyExc_ValueError, "set_state rejected the blob");\n'
    "        return NULL;\n"
    "    }\n"
    "    Py_RETURN_NONE;\n"
    "}"
)
_OLD_STATUS = (
    "    if (_rc != 0) {\n"
    '        PyErr_Format(PyExc_ValueError, "%s (rc=%lld)",\n'
)
_OLD_HANDLE_SET_STATE = (
    "    if (kv_set_state(self->h, PyBytes_AS_STRING(arg)) != 0) {\n"
    '        PyErr_SetString(PyExc_ValueError, "set_state rejected the blob");\n'
    "        return NULL;\n"
    "    }\n"
    "    Py_RETURN_NONE;\n"
    "}"
)


def test_without_a_table_every_face_renders_as_before(tmp_path):
    dest = _object_project(tmp_path / "p")
    ext = _ext(dest)
    assert "switch" not in ext
    assert _OLD_SET_STATE in _fn(ext, "Acq_set_state(")
    assert _OLD_STATUS in _fn(ext, "Acq_set_freq(")
    # No Raises section appears on set_state, whose prose names the class.
    assert "Raises\n" not in _member_doc(_pyi(dest), "set_state")
    assert "or the core rejects it" in _member_doc(_pyi(dest), "set_state")
    # And an emptied table is no table: the tree returns to that text.
    _declare_tables(dest)
    _quiet(apply_run, dest)
    cfg = C.load(dest)
    del cfg["acq"]["set_state_status_errors"]
    for m in cfg["acq"]["methods"]:
        del m["status_errors"]
    del cfg["acq"]["destroy"]
    C.save(dest, cfg)
    _quiet(apply_run, dest)
    assert _ext(dest) == ext


def test_without_rows_the_set_state_check_is_the_original_line():
    assert set_state_check_c("f(h, b)") == set_state_check_c("f(h, b)", [])
    assert set_state_check_c("f(h, b)").startswith("    if (f(h, b) != 0) {")


# -- the docs ------------------------------------------------------------------


def test_the_pyi_raises_section_lists_each_declared_class(tmp_path):
    dest = _object_project(tmp_path / "p")
    _declare_tables(dest)
    _quiet(apply_run, dest)
    pyi = _pyi(dest)
    for member, blanket in (
        ("set_freq", "any other non-zero status"),
        ("set_state", "any other non-zero status"),
    ):
        doc = _member_doc(pyi, member)
        raises = doc[doc.index("Raises\n") :]
        # The prose above defers to that section rather than claiming the
        # core's refusal is always ValueError.
        assert "or the core rejects it" not in doc[: doc.index("Raises\n")]
        assert raises.index("MemoryError") < raises.index("P_ERR_INVALID")
        assert "``P_ERR_MEMORY``" in raises
        assert "``rejected``" in raises
        assert blanket in raises, (member, raises)
    # `__exit__` documents the finalizer's rows too.
    assert "``P_ERR_MEMORY``" in _member_doc(pyi, "__exit__")


def test_a_module_objects_stub_lists_the_set_state_rows(tmp_path):
    """The module `.pyi` is a second writer of the triplet (`_stubs`)."""
    from just_makeit._module import run as module_run

    dest = tmp_path / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    _quiet(module_run, dest, "sig")
    _quiet(
        object_run,
        dest,
        "mix",
        module="sig",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        serializable=True,
        set_state_status_errors=ROWS,
    )
    _quiet(apply_run, dest)
    pyi = next((dest / "src/p").rglob("sig*.pyi")).read_text("utf-8")
    raises = _member_doc(pyi, "set_state")
    assert "MemoryError" in raises and "``P_ERR_MEMORY``" in raises
    assert "or the core rejects it" not in raises
    frag = (dest / "native/src/sig/sig_ext_mix.c").read_text("utf-8")
    assert "case P_ERR_MEMORY:" in _fn(frag, "Mix_set_state(")


# -- every face that reads the key ----------------------------------------------


def test_the_keys_are_recognised():
    assert "set_state_status_errors" in _keys.OBJECT_KEYS
    assert "set_state_status_errors" in _keys.HANDLE_MODULE_KEYS
    assert "status_errors" in _keys.HANDLE_METHOD_KEYS
    cfg = {
        "acq": {
            "serializable": "true",
            "set_state_status_errors": ROWS,
            "methods": [
                {"name": "f", "status_return": True, "status_errors": ROWS}
            ],
        }
    }
    assert _keys.unknown_keys(cfg) == []


def test_jm_script_round_trips_both_tables(tmp_path):
    root = tmp_path / "orig"
    root.mkdir()
    assert run_cli("new", "p", cwd=root).returncode == 0
    proj = root / "p"
    r = run_cli(
        "object",
        "acq",
        "--state",
        "n:int",
        "--serializable",
        "--set-state-status-error",
        "P_ERR_MEMORY:MemoryError",
        "--set-state-status-error",
        "P_ERR_INVALID:ValueError:rejected",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    r = run_cli(
        "method",
        "acq",
        "set_freq",
        "--param",
        "hz:int",
        "--return-type",
        "int",
        "--status-return",
        "--status-error",
        "P_ERR_MEMORY:MemoryError",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    orig, replayed = script_round_trip(proj, tmp_path / "replay")
    assert orig["acq"]["set_state_status_errors"] == ROWS
    assert orig["acq"]["methods"][0]["status_errors"] == ROWS[:1]
    assert orig == replayed


@pytest.mark.parametrize("documented", [True, False])
def test_apply_from_the_manifest_alone_keeps_the_rows(tmp_path, documented):
    """A fresh checkout: the generated binding deleted, `apply` rebuilds it
    from the manifest, and `status --check` agrees with the tree -- which it
    can only do if the replay it compares against carried the key.

    Undocumented is the case that needs `_apply._object_kwargs` to forward
    the key: a documented header makes `apply` re-render `_ext.c` from the
    real manifest afterwards, which hides a replay that dropped it."""
    dest = _object_project(tmp_path / "p")
    _declare_tables(dest)
    _quiet(apply_run, dest)
    if not documented:
        header = dest / INC_ROOT / "acq/acq_core.h"
        text = header.read_text(encoding="utf-8")
        text = re.sub(r"[ \t]*/\*\*.*?\*/\n?", "", text, flags=re.S)
        text = re.sub(r"[ \t]*///<[^\n]*", "", text)
        assert "/**" not in text and "@brief" not in text
        header.write_text(text, encoding="utf-8")
    (dest / "native/src/acq/acq_ext.c").unlink()
    _quiet(apply_run, dest)
    ext = _ext(dest)
    assert "case P_ERR_MEMORY:" in _fn(ext, "Acq_set_state(")
    assert "case P_ERR_MEMORY:" in _fn(ext, "Acq_set_freq(")
    r = run_cli("status", "--check", cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr


# -- refusals -------------------------------------------------------------------


def test_set_state_rows_without_serializable_are_refused(tmp_path):
    dest = _object_project(tmp_path / "p")
    cfg = C.load(dest)
    cfg["acq"]["serializable"] = "false"
    cfg["acq"]["set_state_status_errors"] = ROWS
    C.save(dest, cfg)
    with pytest.raises(Refusal, match="not serializable"):
        C.load(dest)


@pytest.mark.parametrize(
    "extra, spec, needle",
    [
        ((), "P_ERR_MEMORY:MemoryError", "not serializable"),
        # set_state has no scalar in scope: the renderer cannot emit `{n}`,
        # so this must be refused before `_init.run` renders anything.
        (("--state", "n:int", "--serializable"), "E:MemoryError:n={n}",
         "{n}, which is not in scope"),
    ],
)  # fmt: skip
def test_the_cli_refuses_set_state_rows_before_writing(
    tmp_path, extra, spec, needle
):
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    before = sorted(p.name for p in proj.rglob("*"))
    r = run_cli(
        "object", "acq", *extra, "--set-state-status-error", spec, cwd=proj
    )
    assert r.returncode == 1, r.stderr
    assert r.stderr.startswith("error:") and needle in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert sorted(p.name for p in proj.rglob("*")) == before


@pytest.mark.parametrize(
    "rows, needle",
    [
        ([{"status": "E", "error": "Nope"}], "which jm does not emit"),
        ([{"status": "-4", "error": "ValueError"}], "not a C constant"),
        (
            [{"status": "E", "error": "ValueError", "message": "{n}"}],
            "{n}, which is not in scope",
        ),
        (
            [
                {"status": "E", "error": "ValueError"},
                {"status": "E", "error": "OSError"},
            ],
            "maps 'E' twice",
        ),
    ],
)
def test_a_set_state_row_the_shared_validator_refuses(rows, needle):
    cfg = {"acq": {"serializable": "true", "set_state_status_errors": rows}}
    errs = C._set_state_status_errors_errors(cfg)
    assert errs and needle in errs[0], errs


def test_method_rows_go_through_the_shared_validator():
    m = {
        "name": "set_freq",
        "status_return": True,
        "params": [{"name": "hz", "type": "int"}],
        "status_errors": [
            {"status": "E", "error": "ValueError", "message": "{hz} {x}"}
        ],
    }
    why = _borrow.why_not(m, "acq", [])
    assert "{x}, which is not in scope" in why
    m["status_errors"][0]["message"] = "{hz} is bad"
    assert _borrow.why_not(m, "acq", []) == ""
    # `status_fn` stays a borrow's: here the status IS the return.
    assert "Drop `status_fn`" in _borrow.why_not({**m, "status_fn": "f"})
    # A method that returns no status still has nothing to read.
    plain = {k: v for k, v in m.items() if k != "status_return"}
    assert "not a borrow" in _borrow.why_not(plain)


# -- a handle module: method and set_state ---------------------------------------

_KV_H = """\
#ifndef KV_H
#define KV_H
#include <stddef.h>
enum { P_ERR_MEMORY = -2, P_ERR_INVALID = -4 };
typedef struct kv kv_t;
kv_t *kv_open(int k);
void kv_close(kv_t *c);
int kv_tune(kv_t *c, int hz);
size_t kv_state_bytes(const kv_t *c);
void kv_get_state(const kv_t *c, void *blob);
int kv_set_state(kv_t *c, const void *blob);
#endif
"""

#: Each kernel answers by its argument: 1 a MEMORY code, 2 an INVALID one,
#: 3 a code no row names, anything else success.
_KV_C = """\
#include "kv/kv.h"
#include <stdlib.h>
#include <string.h>
struct kv { int n; };
static int code(int v) {
    return v == 1 ? P_ERR_MEMORY : v == 2 ? P_ERR_INVALID : v == 3 ? -9 : 0;
}
kv_t *kv_open(int k) {
    kv_t *c = malloc(sizeof *c);
    if (c) c->n = k;
    return c;
}
void kv_close(kv_t *c) { free(c); }
int kv_tune(kv_t *c, int hz) { (void)c; return code(hz); }
size_t kv_state_bytes(const kv_t *c) { (void)c; return sizeof(int); }
void kv_get_state(const kv_t *c, void *blob) {
    memcpy(blob, &c->n, sizeof c->n);
}
int kv_set_state(kv_t *c, const void *blob) {
    int v;
    memcpy(&v, blob, sizeof v);
    return code(v);
}
"""


def _kv_module(rows: "list[dict] | None") -> dict:
    mod = {
        "kind": "handle",
        "backing": "kv",
        "header": "kv/kv.h",
        "type_name": "Kv",
        "create_fn": "kv_open",
        "close_fn": "kv_close",
        "create_args": [{"name": "k", "type": "int", "default": "0"}],
        "serializable": "true",
        "methods": [
            {
                "name": "tune",
                "fn": "kv_tune",
                "returns": "int",
                "status_return": True,
                "args": [{"name": "hz", "type": "int"}],
            }
        ],
    }
    if rows:
        mod["set_state_status_errors"] = rows
        mod["methods"][0]["status_errors"] = rows
    return mod


def _kv_project(tmp: Path, rows: "list[dict] | None") -> Path:
    from test_handle_build import _placed

    _quiet(new_run, "proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["kv"] = _placed(_kv_module(rows), tmp)
    C.save(tmp, cfg)
    _quiet(apply_run, tmp)
    return tmp


def _kv_text(tmp: Path) -> "tuple[str, str]":
    ext = (tmp / "native/src/kv/kv_ext.c").read_text("utf-8")
    pyi = next((tmp / "src/proj").rglob("kv*.pyi")).read_text("utf-8")
    return ext, pyi


def test_a_handle_without_rows_renders_as_before(tmp_path):
    ext, pyi = _kv_text(_kv_project(tmp_path, None))
    assert "switch" not in ext
    assert _OLD_HANDLE_SET_STATE in _fn(ext, "Kv_set_state(")
    assert '"""Restore mutable state from a get_state() blob."""' in pyi


def test_a_handle_documents_its_rows_on_both_faces(tmp_path):
    ext, pyi = _kv_text(_kv_project(tmp_path, ROWS))
    for member in ("tune", "set_state"):
        doc = _member_doc(pyi, member)
        assert "Raises" in doc and "``P_ERR_MEMORY``" in doc, (member, doc)
    rows = ext[ext.index("static PyMethodDef Kv_methods") :]
    for member in ("tune", "set_state"):
        row = rows[rows.index(f'{{"{member}"') :]
        row = row[: row.index("},")]
        assert "MemoryError" in row, (member, row)


def test_a_handle_refuses_rows_on_a_method_with_no_status(tmp_path):
    mod = _kv_module(ROWS)
    del mod["methods"][0]["status_return"]
    from just_makeit._handle import _check_error_decl

    with pytest.raises(Refusal, match="returns no status"):
        _check_error_decl(mod["methods"][0], "tune", "int")
    m = {**_kv_module(None)["methods"][0]}
    m["status_errors"] = [
        {"status": "E", "error": "ValueError", "message": "{hz}"}
    ]
    with pytest.raises(Refusal, match="not in scope"):
        _check_error_decl(m, "tune", "int")


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_handle_raises_each_declared_class_end_to_end(tmp_path):
    from test_handle_build import _compile_import

    tmp = _kv_project(tmp_path, ROWS)
    kv = _compile_import(tmp, "kv", _KV_H, _KV_C)
    import struct

    h = kv.Kv(0)

    def put(v: int) -> None:
        h.set_state(struct.pack("i", v))

    for call, mem, rest in (
        (
            h.tune,
            r"kv_tune failed \(P_ERR_MEMORY\)",
            r"kv_tune failed \(rc=-9\)",
        ),
        (
            put,
            r"set_state failed \(P_ERR_MEMORY\)",
            "set_state rejected the blob",
        ),
    ):
        assert call(0) is None
        with pytest.raises(MemoryError, match=f"^{mem}$"):
            call(1)
        with pytest.raises(ValueError, match="^rejected$"):
            call(2)
        with pytest.raises(ValueError, match=f"^{rest}$"):
            call(3)


# -- an object, end to end ------------------------------------------------------

_CHECK = r"""
import struct
from p import Acq

def raises(call, cls, text):
    try:
        call()
    except Exception as e:
        assert type(e) is cls and str(e) == text, repr(e)
    else:
        raise AssertionError("no raise")

a = Acq()
assert a.set_freq(0) is None
raises(lambda: a.set_freq(1), MemoryError, "set_freq failed (P_ERR_MEMORY)")
raises(lambda: a.set_freq(2), ValueError, "rejected")
raises(lambda: a.set_freq(3), ValueError, "set_freq failed (rc=-9)")

blob = lambda n: struct.pack("i", n)
assert a.set_state(blob(0)) is None
raises(lambda: a.set_state(blob(1)), MemoryError,
       "set_state failed (P_ERR_MEMORY)")
raises(lambda: a.set_state(blob(2)), ValueError, "rejected")
raises(lambda: a.set_state(blob(3)), ValueError,
       "set_state rejected the blob")

# `__exit__` calls finish(), whose code is the state's n.
for n, cls, text in (
    (1, MemoryError, "finish failed (P_ERR_MEMORY)"),
    (2, ValueError, "rejected"),
    (3, ValueError, "finish failed (rc=-9)"),
):
    b = Acq()
    try:
        b.set_state(blob(n))  # stores n, then refuses with its code
    except Exception:
        pass
    def run():
        with b:
            pass
    raises(run, cls, text)
    raises(b.finish, cls, text)

assert "MemoryError" in Acq.set_freq.__doc__
assert "MemoryError" in Acq.set_state.__doc__
assert "or the core rejects it" not in Acq.set_state.__doc__
print("OK")
"""


def _implement(core: Path, fn: str, body: str) -> None:
    """Replace the body of the C function *fn* in *core*."""
    text = core.read_text(encoding="utf-8")
    m = re.search(rf"\n{fn}\([^)]*\)\n\{{\n", text)
    assert m, fn
    end = text.index("\n}\n", m.end())
    core.write_text(text[: m.end()] + body + text[end:], encoding="utf-8")


def _code(expr: str) -> str:
    return (
        f"    if ({expr} == 1) return P_ERR_MEMORY;\n"
        f"    if ({expr} == 2) return P_ERR_INVALID;\n"
        f"    if ({expr} == 3) return -9;\n"
        "    return 0;"
    )


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_an_object_raises_each_declared_class_end_to_end(tmp_path):
    """Declared in the manifest and materialized by `apply`, so the replay
    forwarding each key is on the path."""
    dest = _object_project(tmp_path / "p")
    _declare_tables(dest)
    header = dest / INC_ROOT / "acq/acq_core.h"
    text = header.read_text(encoding="utf-8")
    anchor = '#include "p/clib_common.h"\n'
    assert text.count(anchor) == 1
    header.write_text(text.replace(anchor, anchor + CODES), encoding="utf-8")
    _quiet(apply_run, dest)
    core = dest / "native/src/acq/acq_core.c"
    _implement(core, "acq_set_freq", "    (void)state;\n" + _code("hz"))
    _implement(core, "acq_finish", _code("state->n"))
    _implement(
        core,
        "acq_set_state",
        "    memcpy(&state->n, blob, sizeof state->n);\n" + _code("state->n"),
    )
    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    out = subprocess.run(
        [sys.executable, "-c", _CHECK],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "OK"
