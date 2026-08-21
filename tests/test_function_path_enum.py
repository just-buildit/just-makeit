"""Codegen unit tests for module-function ``path`` / ``enum`` args (gh-353).

The ``jm function`` generator gained two argument kinds that already existed in
the handle generator (``_handle.py``) and composer: a ``path`` arg (Python
``str | os.PathLike`` coerced with ``PyUnicode_FSConverter`` to a borrowed
``const char *``) and an ``enum`` arg (a choice string validated to its SSOT
int via ``_enum_index``). These tests assert on the *generated text* — the
PyArg format, the FSConverter coercion + post-call ``Py_XDECREF`` (gh-219 UAF),
the per-enum table + the shared ``_enum_index`` helper, the required-before-``|``
ordering, and an honored enum/path default. The compile-and-run harness lives
in ``test_function_path_enum_build.py``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._function import run as function_run
from just_makeit._render import make_functions_ctx
from just_makeit._config import load, save, module_functions
from just_makeit import _stubs


_ENUMS = {"log_kind": ["raw", "json", "csv"]}


def _ctx(fns, enums=None):
    return make_functions_ctx("logm", "Logm", fns, enums)


def test_path_arg_emits_fsconverter_and_post_call_xdecref():
    """A path arg coerces with O& + PyUnicode_FSConverter, passes
    PyBytes_AS_STRING, and XDECREFs the borrow only AFTER the C call (gh-219)."""
    fns = [
        {
            "name": "touch",
            "return_type": "void",
            "params": [{"name": "p", "type": "path"}],
        }
    ]
    w = _ctx(fns)["function_wrappers"]
    assert "PyObject *p = NULL;" in w
    assert "PyUnicode_FSConverter, &p" in w
    assert "touch(PyBytes_AS_STRING(p));" in w
    # The XDECREF runs after the call, before Py_RETURN_NONE.
    call_idx = w.index("touch(PyBytes_AS_STRING(p));")
    decref_idx = w.index("Py_XDECREF(p);", call_idx)
    none_idx = w.index("Py_RETURN_NONE;", call_idx)
    assert call_idx < decref_idx < none_idx


def test_path_scalar_return_xdecref_after_call():
    """A scalar-returning function captures the C result into a temp, THEN
    XDECREFs the path borrow (the C side copied the string during the call)."""
    fns = [
        {
            "name": "size_of",
            "return_type": "size_t",
            "params": [{"name": "p", "type": "path"}],
        }
    ]
    w = _ctx(fns)["function_wrappers"]
    # Result captured into a temp before cleanup.
    assert "_r = size_of(PyBytes_AS_STRING(p));" in w
    r_idx = w.index("_r = size_of(PyBytes_AS_STRING(p));")
    decref_idx = w.index("Py_XDECREF(p);", r_idx)
    ret_idx = w.index("return ", decref_idx)
    assert r_idx < decref_idx < ret_idx


def test_path_parse_fail_xdecrefs_in_braced_block():
    """The parse-fail path XDECREFs the path object in a braced block (so the
    return is guarded, not run unconditionally)."""
    fns = [
        {
            "name": "touch",
            "return_type": "void",
            "params": [{"name": "p", "type": "path"}],
        }
    ]
    w = _ctx(fns)["function_wrappers"]
    assert "    {\n        Py_XDECREF(p);\n        return NULL;\n    }" in w


def test_enum_arg_emits_index_lookup_table_and_helper():
    """An enum arg parses with `s`, validates to its SSOT int via _enum_index,
    and the ext.c emits the per-enum table + the shared helper."""
    fns = [
        {
            "name": "open_log",
            "return_type": "void",
            "params": [{"name": "kind", "type": "int", "enum": "log_kind"}],
        }
    ]
    ctx = _ctx(fns, _ENUMS)
    w = ctx["function_wrappers"]
    tables = ctx["function_enum_tables"]
    assert ctx["function_uses_enum"] is True
    assert 'const char *kind = "";' in w
    assert "int _arg_kind = _enum_index(_enum_log_kind, kind);" in w
    assert "if (_arg_kind < 0)" in w
    # gh-1026: the refusal NAMES the choices, as a method parameter for the
    # same enum has since gh-1021. This assertion used to demand the shorter
    # wording, which is how one manifest came to produce two spellings of one
    # refusal depending on which surface the enum was declared on.
    assert "\"invalid kind '%s' (choices: raw, json, csv)\", kind);" in w
    assert "open_log(_arg_kind);" in w
    # The SSOT helper + table go into the tables block (emitted before wrappers).
    assert "_enum_index(const char *const *tab, const char *s)" in tables
    assert "static const char *const _enum_log_kind[] = {" in tables
    assert '    "raw",\n    "json",\n    "csv",\n    NULL,' in tables


def test_enum_free_module_emits_no_tables_and_no_enum_flag():
    """A function with no enum param emits no _enum_index helper, no tables, and
    reports function_uses_enum False (byte-identical to pre-gh-353)."""
    fns = [
        {
            "name": "plain",
            "return_type": "double",
            "params": [{"name": "x", "type": "double"}],
        }
    ]
    ctx = _ctx(fns, _ENUMS)
    assert ctx["function_enum_tables"] == ""
    assert ctx["function_uses_enum"] is False
    assert "_enum_index" not in ctx["function_wrappers"]


def test_required_path_before_optional_bar():
    """A required path arg precedes the `|` in the PyArg format; a defaulted
    scalar after it lands after the `|` (gh-240 ordering, unchanged)."""
    fns = [
        {
            "name": "f",
            "return_type": "void",
            "params": [
                {"name": "p", "type": "path"},
                {"name": "n", "type": "int", "default": "3"},
            ],
        }
    ]
    w = _ctx(fns)["function_wrappers"]
    assert '"O&|i"' in w


def test_enum_default_honored_in_format_and_decl():
    """An enum arg with a default is optional: its `s` lands after the `|` and
    its C local initializes to the default choice string."""
    fns = [
        {
            "name": "g",
            "return_type": "void",
            "params": [
                {"name": "x", "type": "double"},
                {
                    "name": "kind",
                    "type": "int",
                    "enum": "log_kind",
                    "default": "json",
                },
            ],
        }
    ]
    w = _ctx(fns, _ENUMS)["function_wrappers"]
    assert '"d|s"' in w
    assert 'const char *kind = "json";' in w


def test_c_decl_path_is_const_char_star():
    """The generated C declaration types a path arg as `const char *`, an enum
    arg as a plain `int` (both via fn_c_decl)."""
    from just_makeit._render import fn_c_decl

    decl = fn_c_decl(
        "open_log",
        [("p", "path", False, "", ""), ("k", "int", False, "", "log_kind")],
        "size_t",
    )
    assert "const char *p" in decl
    assert "int k" in decl


# ── round-trip: CLI tuple -> manifest -> stub ────────────────────────────────


@pytest.fixture()
def logm_root(tmp_path):
    """A project with a `logm` module and a declared [[enum]]."""
    root = tmp_path / "proj"
    new_run("proj", root, modules=["logm"])
    cfg = load(root)
    cfg.setdefault("enum", []).append(
        {"name": "log_kind", "values": ["raw", "json", "csv"]}
    )
    save(root, cfg)
    return root


def test_run_stores_path_and_enum_in_manifest(logm_root):
    """_function.run() persists `type:"path"` and the enum name into the
    manifest param dicts."""
    function_run(
        logm_root,
        "open_log",
        "logm",
        params=[
            ("p", "path", False, "", ""),
            ("kind", "int", False, "", "log_kind"),
        ],
        return_type="size_t",
    )
    cfg = load(logm_root)
    fns = module_functions(cfg, "logm")
    params = fns[0]["params"]
    assert params[0] == {"name": "p", "type": "path"}
    assert params[1] == {"name": "kind", "type": "int", "enum": "log_kind"}


def test_run_rejects_undeclared_enum(logm_root):
    """An enum name with no matching [[enum]] is rejected with a clear error."""
    with pytest.raises(SystemExit):
        function_run(
            logm_root,
            "bad",
            "logm",
            params=[("kind", "int", False, "", "nope")],
        )


def test_ext_c_and_apply_round_trip_keep_enum_path(logm_root):
    """The generated module ext.c carries the enum tables + path/enum handling,
    and `jm apply` regeneration preserves them (the 5-tuple replay in
    _apply.py)."""
    from just_makeit._apply import run as apply_run

    function_run(
        logm_root,
        "make_code",
        "logm",
        params=[
            ("name", "path", False, "", ""),
            ("kind", "int", False, "json", "log_kind"),
        ],
        return_type="size_t",
    )
    ext_c = (logm_root / "native" / "src" / "logm" / "logm_ext.c").read_text(
        encoding="utf-8"
    )
    assert "static const char *const _enum_log_kind[] = {" in ext_c
    assert "_enum_index(_enum_log_kind, kind)" in ext_c
    assert "PyUnicode_FSConverter, &name" in ext_c
    assert "#include <string.h>" in ext_c

    # jm apply replays _function.run() from the manifest; the 5-tuple replay
    # must keep the enum/path handling (otherwise these drop silently).
    apply_run(logm_root)
    ext_c2 = (logm_root / "native" / "src" / "logm" / "logm_ext.c").read_text(
        encoding="utf-8"
    )
    assert "static const char *const _enum_log_kind[] = {" in ext_c2
    assert "_enum_index(_enum_log_kind, kind)" in ext_c2
    assert "PyUnicode_FSConverter, &name" in ext_c2


def test_script_reconstructs_path_and_enum_syntax(logm_root):
    """`jm script` reconstructs the --param path / enum:<e>[=default] syntax."""
    from just_makeit import _script

    function_run(
        logm_root,
        "make_code",
        "logm",
        params=[
            ("name", "path", False, "", ""),
            ("kind", "int", False, "json", "log_kind"),
        ],
        return_type="size_t",
    )
    cfg = load(logm_root)
    fn = module_functions(cfg, "logm")[0]
    flags = _script._function_flags(fn, "logm")
    joined = " ".join(flags)
    assert "name:path" in joined
    assert "kind:enum:log_kind=json" in joined


def test_pyi_path_and_enum_annotations(logm_root):
    """The module-function stub annotates a path arg as `str | os.PathLike`
    (and imports os) and an enum arg as `str`."""
    function_run(
        logm_root,
        "open_log",
        "logm",
        params=[
            ("p", "path", False, "", ""),
            ("kind", "int", False, "json", "log_kind"),
        ],
        return_type="size_t",
    )
    cfg = load(logm_root)
    pyi = _stubs.make_module_pyi(cfg, "logm")
    assert "import os" in pyi
    assert "p: str | os.PathLike" in pyi
    assert "kind: str" in pyi
    assert "kind: str = 'json'" in pyi
