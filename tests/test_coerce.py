"""Tests for the shared argument-coercion primitives (_coerce, gh-353).

The file/path handler is emitted by two generators — the handle generator
(_handle, for a `path` create-arg/method) and the module-function generator
(_render, for a `jm function` path param). These assert the single shared
source produces the exact C fragments both rely on, and that both generators
actually route through it (so the pattern can't drift).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _coerce, _handle, _render


def test_path_primitives():
    assert _coerce.PATH_C_TYPE == "const char *"
    assert (
        _coerce.path_decl("p") == "PyObject *p = NULL;  /* fspath -> bytes */"
    )
    assert _coerce.path_fmt() == "O&"
    assert _coerce.path_addr("p") == "PyUnicode_FSConverter, &p"
    assert _coerce.path_call_expr("p") == "PyBytes_AS_STRING(p)"
    assert _coerce.path_release("p") == "Py_XDECREF(p);"


def test_bytes_primitives():
    # gh-565: an opaque-bytes arg crosses as a borrowed (const void*, size_t)
    # pair via y#. No release step — y# borrows the buffer for the call.
    assert _coerce.BYTES_C_TYPE == "const void *"
    assert _coerce.bytes_decl("b") == [
        "const char *b = NULL;  /* borrowed bytes buffer */",
        "Py_ssize_t b_len = 0;",
    ]
    assert _coerce.bytes_fmt() == "y#"
    assert _coerce.bytes_addr("b") == "&b, &b_len"
    assert _coerce.bytes_call_exprs("b") == "(const void *)b, (size_t)b_len"


def test_handle_path_arg_routes_through_coerce():
    a = {"name": "path", "type": "path"}
    assert _coerce.path_fmt() == _handle._arg_fmt(a)
    assert _coerce.path_addr("path") == _handle._arg_addr(a)
    assert _coerce.path_call_expr("path") == _handle._create_call_arg(a)
    assert _coerce.path_decl("path") in _handle._arg_decl(a)


def test_function_binding_path_arg_uses_coerce():
    parse, call, cleanup = _render._build_params_parse(
        [{"name": "path", "type": "path"}]
    )
    # The same O& / FSConverter / PyBytes primitives, and the borrow released
    # only in the post-call cleanup (gh-219).
    assert _coerce.path_addr("path") in parse
    assert _coerce.path_call_expr("path") in call
    assert _coerce.path_release("path") in cleanup


def test_function_c_param_path_type():
    # The C signature a path param presents to user C code is the shared type.
    decl = _render.fn_c_decl("save", [("path", "path")], "void")
    assert f"{_coerce.PATH_C_TYPE}path" in decl
