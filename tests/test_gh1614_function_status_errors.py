"""A module function's status names its exception (gh-1614).

``check_return = true`` raised ``RuntimeError("<fn> failed (rc=N)")`` for
every non-zero status, so doppler's ``util.simpson_weights`` refusing an
even-length ``w`` with ``DP_ERR_INVALID`` -- a bad argument, which Python
spells ``ValueError`` -- raised ``RuntimeError``. ``why = true`` (gh-1706)
answers half of it: a refusal that writes a sentence raises ``ValueError``,
one that writes none keeps ``RuntimeError``. The status still chose nothing,
so ``DP_ERR_MEMORY`` could not be ``MemoryError``, and a runtime failure that
explained itself read as bad input.

The answer is the table methods already have (gh-1418): ``status_errors``
rows, now also on a ``check_return`` function, where the status is the
return value itself rather than a ``status_fn``'s answer. Same rows, same
validator (`_borrow.rows_why_not`), same emitter (`_borrow.status_cases_c`).
A status with no row keeps the ``check_return`` error; under ``why`` the row
picks the class and the C function's sentence the text.

The end-to-end test builds a real generated project and asserts each
exception's CLASS and MESSAGE.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _borrow  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit import _script  # noqa: E402

_PARAMS = [{"name": "n", "type": "int"}]
_ROWS = [
    {"status": "E_BAD", "error": "ValueError", "message": "n={n} is bad"},
    {"status": "E_NOMEM", "error": "MemoryError"},
]


def _wrap(**kw):
    return R._py_wrapper_for_function(
        "f", _PARAMS, kw.pop("return_type", "int"), c_name="p_f", **kw
    )


# -- the binding ----------------------------------------------------------------


def test_rows_switch_on_the_status_before_the_generic_raise():
    src = _wrap(check_return=True, status_errors=_ROWS)
    i_call = src.index("int _rc = p_f(n);")
    i_switch = src.index("switch (_rc) {")
    i_bad = src.index("case E_BAD:")
    i_bad_raise = src.index("PyErr_Format(PyExc_ValueError,")
    i_nomem = src.index("case E_NOMEM:")
    i_nomem_raise = src.index("PyErr_SetString(PyExc_MemoryError,")
    i_default = src.index("default: break;")
    i_fallback = src.index('"p_f failed (rc=%d)"')
    assert (
        i_call
        < i_switch
        < i_bad
        < i_bad_raise
        < i_nomem
        < i_nomem_raise
        < i_default
        < i_fallback
    )
    # The message slot reads the param the caller passed.
    assert '"n=%lld is bad"' in src
    assert "(long long)n" in src
    # A row with no message names the C function and the status.
    assert '"p_f failed (E_NOMEM)"' in src


def test_without_rows_the_binding_is_unchanged():
    assert _wrap(check_return=True, status_errors=[]) == _wrap(
        check_return=True
    )
    assert "switch" not in _wrap(check_return=True)


def test_under_why_the_row_picks_the_class_and_the_sentence_the_text():
    src = _wrap(check_return=True, why=True, status_errors=_ROWS[1:])
    case = src[src.index("case E_NOMEM:") : src.index("default: break;")]
    assert case.index("if (_why)") < case.index(
        "PyErr_SetString(PyExc_MemoryError, _why);"
    )
    assert "PyExc_ValueError" not in case
    assert case.rstrip().endswith("return NULL;")
    # An unlisted status still raises the sentence as ValueError (gh-1706).
    tail = src[src.index("default: break;") :]
    assert "PyErr_SetString(PyExc_ValueError, _why);" in tail


def test_the_borrow_face_renders_its_rows_through_the_same_emitter():
    """One emitter for both faces: the borrow's rows are exactly
    `status_cases_c`'s output, so a fix to the raise reaches both."""
    m = {
        "name": "wait",
        "borrow": True,
        "params": [{"name": "n", "type": "size_t"}],
        "status_fn": "st",
        "status_errors": _ROWS,
    }
    rows = _borrow.status_cases_c(_ROWS, "wait", _borrow.message_slots(m))
    assert rows in _borrow.status_dispatch_c(m)


# -- refusals: the rows must be read, and readable ------------------------------


@pytest.mark.parametrize(
    "fn, needle",
    [
        ({}, "never reads a status"),
        ({"check_return": True, "out_type": "double"}, "never reads"),
        (
            {"check_return": True, "result_fields": [{"name": "a"}]},
            "never reads",
        ),
        (
            {
                "check_return": True,
                "status_errors": [{"status": "E", "error": "Nope"}],
            },
            "which jm does not emit",
        ),
        (
            {
                "check_return": True,
                "status_errors": [{"status": "-4", "error": "ValueError"}],
            },
            "is not a C constant name",
        ),
        (
            {
                "check_return": True,
                "status_errors": [{"status": "0", "error": "ValueError"}],
            },
            "could never fire",
        ),
        (
            {
                "check_return": True,
                "status_errors": [
                    {"status": "E", "error": "ValueError", "message": "{x}"}
                ],
            },
            "{x}, which is not in scope",
        ),
        (
            {
                "check_return": True,
                "status_errors": [
                    {"status": "E", "error": "ValueError"},
                    {"status": "E", "error": "OSError"},
                ],
            },
            "maps 'E' twice",
        ),
    ],
)
def test_a_table_that_cannot_be_honoured_is_refused(fn, needle):
    fn = {
        "name": "f",
        "return_type": "int",
        "params": _PARAMS,
        "status_errors": _ROWS,
        **fn,
    }
    why = R.status_errors_why_not(fn)
    assert needle in why, why
    assert why.startswith("function 'f'")
    with pytest.raises(ValueError, match="function 'f'"):
        _wrap(
            check_return=bool(fn.get("check_return")),
            out_type=fn.get("out_type", ""),
            result_fields=fn.get("result_fields"),
            status_errors=fn["status_errors"],
        )


# -- the declaration faces ------------------------------------------------------


def test_the_key_is_recognised_on_a_function():
    assert "status_errors" in _keys.FUNCTION_KEYS
    fn = {"name": "f", "check_return": True, "status_errors": _ROWS}
    cfg = {"module": {"m": {"objects": [], "functions": [fn]}}}
    assert _keys.unknown_keys(cfg) == []


def test_script_replays_each_row_once():
    """`_record_flags` is the one writer of ``--status-error``, shared with
    the method face, so a row is spelled once -- a second writer in
    ``_function_flags`` would replay every row twice and the CLI would then
    refuse the duplicated status."""
    flags = "".join(
        _script._function_flags(
            {"name": "f", "check_return": True, "status_errors": _ROWS}, "m"
        )
    )
    assert flags.count('--status-error "E_BAD:ValueError:n={n} is bad"') == 1
    assert flags.count("--status-error E_NOMEM:MemoryError") == 1


def test_the_manifest_writer_keeps_the_rows():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib

    from just_makeit import _config as C

    fn = {"name": "f", "return_type": "int", "check_return": True}
    fn["status_errors"] = _ROWS
    cfg = {
        "project": {"name": "p", "version": "0.1.0"},
        "module": {"m": {"objects": [], "functions": [fn]}},
    }
    back = tomllib.loads(C._dump(cfg))["module"]["m"]["functions"][0]
    assert back["status_errors"] == _ROWS


def _project(tmp_path: Path) -> Path:
    from just_makeit._module import run as mod_run
    from just_makeit._new import run as new_run

    dest = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest)
        mod_run(dest, "m")
    return dest


def _cli(dest: Path, monkeypatch, *extra: str) -> None:
    from just_makeit import _cli_function

    monkeypatch.chdir(dest)
    with contextlib.redirect_stdout(io.StringIO()):
        _cli_function.run(
            ["f", "--module", "m", "--param", "n:int", "--return-type", "int"]
            + list(extra)
        )


def test_cli_declares_the_rows(tmp_path, monkeypatch):
    from just_makeit._config import load

    dest = _project(tmp_path)
    _cli(
        dest,
        monkeypatch,
        "--check-return",
        "--status-error",
        "E_BAD:ValueError:n={n} is bad",
        "--status-error",
        "E_NOMEM:MemoryError",
    )
    assert load(dest)["module"]["m"]["functions"][0]["status_errors"] == _ROWS
    ext = (dest / "native/src/m/m_ext.c").read_text(encoding="utf-8")
    assert "case E_NOMEM:" in ext


def test_cli_refuses_rows_nothing_reads_before_writing(
    tmp_path, monkeypatch, capsys
):
    from just_makeit._config import load

    dest = _project(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _cli(dest, monkeypatch, "--status-error", "E_BAD:ValueError")
    assert exc.value.code == 1
    assert "never reads a status" in capsys.readouterr().err
    assert not load(dest)["module"]["m"].get("functions")
    assert not (dest / "native/src/m/f.c").exists()


# -- end to end: build, import, read the class and the message ------------------


def _skip_reason() -> str | None:
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

_FUNCTIONS = """
[[module.m.functions]]
name = "weights"
return_type = "int"
check_return = true
status_errors = [
  {status = "P_ERR_INVALID", error = "ValueError", message = "n={n} must be odd"},
  {status = "P_ERR_MEMORY", error = "MemoryError"},
]

[[module.m.functions.params]]
name = "n"
type = "int"

[[module.m.functions]]
name = "hermite"
return_type = "int"
check_return = true
why = true
status_errors = [
  {status = "P_ERR_INVALID", error = "ValueError"},
  {status = "P_ERR_IO", error = "OSError"},
]

[[module.m.functions.params]]
name = "n"
type = "int"
"""

#: The codes are the author's, declared in the module header the binding
#: includes -- jm knows none of them (its `clib_common.h` has no error codes).
_CODES = "enum { P_ERR_MEMORY = -2, P_ERR_INVALID = -4, P_ERR_IO = -5 };\n"

_WEIGHTS_BODY = """
    if (n % 2 == 0) return P_ERR_INVALID;
    if (n < 0) return P_ERR_MEMORY;
    if (n > 100) return -9; /* no row */
    return 0;"""

_HERMITE_BODY = """
    if (n == 1) {
        if (why) *why = "z and p must have the same length";
        return P_ERR_INVALID;
    }
    if (n == 2) {
        if (why) *why = "the backing store is unavailable";
        return P_ERR_IO;
    }
    if (n == 3) return P_ERR_IO; /* a row, and no sentence */
    if (n == 4) {
        if (why) *why = "an unlisted refusal";
        return -9;
    }
    if (n == 5) return -9; /* no row, no sentence */
    return 0;"""

_CHECK = r"""
from p import m

def raises(call, cls, text):
    try:
        call()
    except Exception as e:
        assert type(e) is cls and str(e) == text, repr(e)
    else:
        raise AssertionError("no raise")

assert m.weights(3) is None
raises(lambda: m.weights(4), ValueError, "n=4 must be odd")
raises(lambda: m.weights(-1), MemoryError, "p_weights failed (P_ERR_MEMORY)")
raises(lambda: m.weights(101), RuntimeError, "p_weights failed (rc=-9)")

assert m.hermite(0) is None
raises(lambda: m.hermite(1), ValueError, "z and p must have the same length")
raises(lambda: m.hermite(2), OSError, "the backing store is unavailable")
raises(lambda: m.hermite(3), OSError, "p_hermite failed (P_ERR_IO)")
raises(lambda: m.hermite(4), ValueError, "an unlisted refusal")
raises(lambda: m.hermite(5), RuntimeError, "p_hermite failed (rc=-9)")
print("OK")
"""


def _implement(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index("{\n", text.index("<<IMPLEMENT")) + 2
    end = text.rindex("}")
    path.write_text(text[:start] + body.lstrip("\n") + "\n" + text[end:])


def _build(dest: Path) -> None:
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
        assert r.returncode == 0, f"{cmd[:2]} failed:\n{r.stdout}\n{r.stderr}"


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_each_status_raises_its_declared_exception_end_to_end(tmp_path):
    """Declared in the manifest and materialized by ``apply``, so the replay
    forwarding ``status_errors`` is on the path: dropped there, every status
    raises the ``check_return`` RuntimeError again."""
    from just_makeit._apply import run as apply_run

    dest = _project(tmp_path)
    manifest = dest / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _FUNCTIONS, encoding="utf-8"
    )
    header = dest / "native/inc/p/m/m_core.h"
    text = header.read_text(encoding="utf-8")
    anchor = '#include "p/clib_common.h"\n'
    assert text.count(anchor) == 1
    header.write_text(text.replace(anchor, anchor + _CODES), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(dest)
    _implement(dest / "native/src/m/weights.c", _WEIGHTS_BODY)
    _implement(dest / "native/src/m/hermite.c", _HERMITE_BODY)
    _build(dest)
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
