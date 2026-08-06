"""gh-601 — a warning condition may be an expression, like a property's expr.

Two manifest keys that both name a place in the state struct disagreed about
what they accept, and the strict one was the one that needed to be flexible.

A property takes arbitrary C::

    [[burst_acq.properties]]
    name = "code_bins"
    expr = "self->handle->engine->code_bins"     # fine, any expression

A warning condition had to be a bare identifier::

    error: --condition 'engine->underpowered' is not a C identifier.

The shape that must reach through a pointer is the **forwarder** — an object
whose state struct is a handle onto a shared engine::

    typedef struct {
        acq_state_t *engine;
    } burst_acq_state_t;

There is no bool field on that struct and there never will be: adding one
would duplicate state that already lives on the engine and have to be kept in
sync. So all 22 of doppler's `burst_acq` properties go through `expr`, and its
single warning was the only thing in the file that could not be declared —
leaving one hand-written block in an otherwise fully generated fragment, to be
re-applied by hand after every regeneration. The reporter lost it once
already, and a regeneration is exactly when nobody is looking for a missing
runtime warning.

Its sibling object — same engine, same warning, but a state struct with real
fields — declares it in one line. Two front doors onto one engine could not be
written the same way, purely because of how their structs are shaped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._diagnostics import (  # noqa: E402
    condition_expr,
    make_warnings_ctx,
)

FORWARDER = "self->handle->engine->underpowered"


def _warning(cond: str) -> list[dict]:
    return [
        {
            "after": "__init__",
            "condition": cond,
            "category": "UserWarning",
            "message": "under-powered",
        }
    ]


class TestTheBareNameIsUnchanged:
    """Every manifest written before this must render byte-for-byte as it
    did — the relaxation is additive or it is a migration."""

    def test_an_identifier_still_reaches_through_the_handle(self):
        assert condition_expr("underpowered") == "self->handle->underpowered"

    def test_the_rendered_block_is_unchanged(self):
        block = make_warnings_ctx("acq", "Acq", _warning("underpowered"))[
            "init_warn_block"
        ]
        assert block.startswith("    if (self->handle->underpowered) {\n")

    @pytest.mark.parametrize("name", ["a", "_x", "under_powered", "f2", "_"])
    def test_every_identifier_shape_is_still_sugar(self, name):
        assert condition_expr(name) == f"self->handle->{name}"


class TestAnExpressionIsUsedVerbatim:
    def test_the_forwarder_reach_is_preserved(self):
        assert condition_expr(FORWARDER) == FORWARDER

    def test_it_renders_into_the_if(self):
        block = make_warnings_ctx("acq", "Acq", _warning(FORWARDER))[
            "init_warn_block"
        ]
        assert block.startswith(f"    if ({FORWARDER}) {{\n")
        assert "self->handle->self->handle" not in block, (
            "the expression must not be prefixed a second time"
        )

    @pytest.mark.parametrize(
        "expr",
        [
            "self->handle->engine->underpowered",
            "engine->underpowered",
            "self->handle->n > 0",
            "!self->handle->ready",
            "self->handle->a && self->handle->b",
        ],
    )
    def test_expressions_pass_through(self, expr):
        assert condition_expr(expr) == expr


class TestWhatIsStillRejected:
    """The relaxation is to expressions, not to statements. A statement
    spliced into `if (...)` produces broken C in generated code the author
    did not write — the gh-625 failure mode."""

    @pytest.mark.parametrize("bad", ["a; b", "{ a }", "if (x) {"])
    def test_a_statement_is_refused(self, bad, tmp_path, capsys):
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run
        from just_makeit._warning import run as warning_run

        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "acq", None, state_vars=[("g", "double", "1.0")])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            warning_run(root, "acq", bad, "under-powered")
        assert exc.value.code == 1
        assert "is not a C expression" in capsys.readouterr().err

    def test_an_empty_condition_is_refused(self, tmp_path, capsys):
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run
        from just_makeit._warning import run as warning_run

        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "acq", None, state_vars=[("g", "double", "1.0")])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            warning_run(root, "acq", "   ", "under-powered")
        assert "--condition is required" in capsys.readouterr().err


class TestTheCrossCheckIsScoped:
    """jm warns when a bare condition names no declared field. A full
    expression names its own reach — through a forwarder's `engine->`, which
    is by definition not a field jm declared — so cross-checking it would warn
    on every correct use, which is worse than not checking."""

    def _project(self, tmp_path):
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "acq", None, state_vars=[("g", "double", "1.0")])
        return root

    def test_an_undeclared_bare_name_still_warns(self, tmp_path, capsys):
        from just_makeit._warning import run as warning_run

        root = self._project(tmp_path)
        capsys.readouterr()
        warning_run(root, "acq", "underpowered", "under-powered")
        assert "is not a declared state field" in capsys.readouterr().err

    def test_a_forwarder_expression_does_not(self, tmp_path, capsys):
        from just_makeit._warning import run as warning_run

        root = self._project(tmp_path)
        capsys.readouterr()
        warning_run(root, "acq", FORWARDER, "under-powered")
        assert "is not a declared state field" not in capsys.readouterr().err

    def test_it_lands_in_the_manifest_intact(self, tmp_path, capsys):
        from just_makeit import _config as C
        from just_makeit._warning import run as warning_run

        root = self._project(tmp_path)
        warning_run(root, "acq", FORWARDER, "under-powered")
        assert FORWARDER in (root / C.FILENAME).read_text(), (
            "a TOML round-trip must not mangle the arrows"
        )
