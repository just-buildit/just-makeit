"""gh-514: a kind="handle" module must honour create_error/create_error_message.

gh-482 (0.30.0) let a component translate a NULL ``create()`` into the exception
it actually meant. That plumbing only ever reached objects: a handle module
hardcoded ``RuntimeError: "<create_fn> failed"``, so setting either key on
``[module.<name>]`` silently did nothing.

The keys were invisible rather than ignored-by-design — ``create_error(cfg, comp)``
reads ``cfg[comp]`` while a handle's keys live under ``cfg["module"][name]`` — so
the fix adds handle-scoped accessors and shares the *declared* rendering with the
object path. A handle module is exactly the shape that opens external resources,
so a meaningful open-failure message matters most there.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._context import make_errors_ctx
from just_makeit._new import run as new_run

_MSG = "cannot open capture: no such file or an unsupported BLUE format mode"


def _handle_project(root: Path, extra: str = "") -> str:
    """Scaffold a handle module and return its generated _ext.c text."""
    new_run("probe", root)
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"""
[module.wfm_reader]
kind = "handle"
backing = "wfm_reader"
create_fn = "wfm_reader_open"
{extra}
[[module.wfm_reader.create_args]]
name = "path"
type = "path"
""",
        encoding="utf-8",
    )
    apply_run(root)
    return (
        root / "native" / "src" / "wfm_reader" / "wfm_reader_ext.c"
    ).read_text(encoding="utf-8")


# ── the accessors ──────────────────────────────────────────────────────────


def test_handle_accessor_reads_the_module_section():
    """The root cause: a handle's keys are not where `create_error` looks."""
    cfg = {
        "module": {
            "wfm_reader": {
                "create_error": "ValueError",
                "create_error_message": _MSG,
            }
        }
    }
    assert C.handle_create_error(cfg, "wfm_reader") == "ValueError"
    assert C.handle_create_error_message(cfg, "wfm_reader") == _MSG
    # The object-scoped accessor cannot see them — this is why gh-514 happened.
    assert C.create_error(cfg, "wfm_reader") == ""


# ── generated C ────────────────────────────────────────────────────────────


def test_declared_create_error_is_emitted(tmp_path):
    """The headline fix: the declared exception and message reach the glue."""
    ext = _handle_project(
        tmp_path / "p",
        f'create_error = "ValueError"\ncreate_error_message = "{_MSG}"\n',
    )
    assert "PyErr_SetString(PyExc_ValueError," in ext
    # The message is emitted as a wrapped C string literal, so match a fragment.
    assert "cannot open capture" in ext
    assert 'PyExc_RuntimeError, "wfm_reader_open failed"' not in ext


def test_undeclared_keeps_the_historical_text(tmp_path):
    """Undeclared must stay byte-identical — no churn for existing modules.

    A handle's historical failure is a one-line ``RuntimeError``, not the
    object path's two-line ``MemoryError``; sharing the renderer must not
    quietly migrate every existing handle module to the other shape.
    """
    ext = _handle_project(tmp_path / "p")
    assert (
        '        PyErr_SetString(PyExc_RuntimeError, "wfm_reader_open failed");'
        in ext
    )
    assert "PyExc_MemoryError" not in ext


def test_bad_category_is_a_jm_diagnostic(tmp_path, capsys):
    """A typo must not reach the compiler as an undeclared identifier."""
    root = tmp_path / "p"
    with pytest.raises(SystemExit):
        _handle_project(root, 'create_error = "ValuError"\n')
    err = capsys.readouterr().err
    assert "not a recognised exception" in err
    assert "ValueError" in err  # the supported list is shown


# ── the shared renderer keeps the object path intact ───────────────────────


def test_object_rendering_is_unchanged():
    """`make_errors_ctx`'s new params must default to the object behaviour."""
    out = make_errors_ctx("acq")["create_fail_block"]
    assert out == (
        "    if (!self->handle) {\n"
        "        PyErr_SetString(PyExc_MemoryError,\n"
        '                        "acq_create returned NULL");\n'
        "        return -1;\n"
        "    }\n"
    )


def test_handle_expr_and_undeclared_body_compose():
    """The handle shape: `self->h` plus its own undeclared body."""
    out = make_errors_ctx(
        "wfm_reader",
        handle_expr="self->h",
        undeclared_body='        PyErr_SetString(PyExc_RuntimeError, "x");\n',
    )["create_fail_block"]
    assert out == (
        "    if (!self->h) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "x");\n'
        "        return -1;\n"
        "    }\n"
    )
    # A declared category still wins over the undeclared body.
    declared = make_errors_ctx(
        "wfm_reader",
        "ValueError",
        "boom",
        handle_expr="self->h",
        undeclared_body='        PyErr_SetString(PyExc_RuntimeError, "x");\n',
    )["create_fail_block"]
    assert "PyExc_ValueError" in declared
    assert "PyExc_RuntimeError" not in declared
