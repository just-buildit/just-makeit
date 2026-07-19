"""Tests for object-level ``create_fn`` — a plain object's C constructor
override (gh-509).

The feature exists because a plain object whose backing constructor is not the
default ``<comp>_create`` (doppler's ``acq`` -> ``acq_create_continuous``) used
to need a hand-patch in the generated ``_ext.c`` — which regeneration silently
dropped, reverting the call to a ``<comp>_create`` that does not even exist. So
the load-bearing test is not "does the create_line name it" but
``TestSurvivesRegeneration``: the override has to come back from the manifest
alone, and ``jm status --check`` stays clean.
"""

from __future__ import annotations

import io
import contextlib

import pytest

from just_makeit import _config as C

tomllib = C.tomllib
from just_makeit import _context as Ctx
from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._apply import run as apply_run
from just_makeit._script import run as script_run
from just_makeit._status import run as status_run


# ── config layer ────────────────────────────────────────────────────────────


class TestConfigLayer:
    def test_accessor_default_is_none(self):
        cfg = {"widget": {"no_state": "true"}}
        assert C.object_create_fn(cfg, "widget") is None

    def test_accessor_returns_override(self):
        cfg = {"widget": {"create_fn": "widget_forge"}}
        assert C.object_create_fn(cfg, "widget") == "widget_forge"

    def test_dump_reload_roundtrip(self):
        cfg = {
            "project": {"name": "demo", "version": "0.1.0"},
            "widget": {
                "arg_type": "float _Complex",
                "return_type": "float _Complex",
                "no_state": "true",
                "create_fn": "widget_forge",
                "state": [],
            },
        }
        reloaded = tomllib.loads(C._dump(cfg))
        assert C.object_create_fn(reloaded, "widget") == "widget_forge"

    def test_no_create_fn_emits_nothing(self):
        cfg = {
            "project": {"name": "demo", "version": "0.1.0"},
            "widget": {"no_state": "true", "state": []},
        }
        assert "create_fn" not in C._dump(cfg)


# ── generation ──────────────────────────────────────────────────────────────


class TestGeneration:
    _sv = [("gain", "double", "1.0")]

    def test_default_create_line(self):
        ctx = Ctx.make_state_ctx("widget", "Widget", self._sv)
        assert "widget_create(" in ctx["create_line"]

    def test_override_create_line(self):
        ctx = Ctx.make_state_ctx(
            "widget", "Widget", self._sv, create_fn="widget_forge"
        )
        assert "widget_forge(" in ctx["create_line"]
        assert "widget_create(" not in ctx["create_line"]

    def test_error_message_names_override(self):
        # gh-509: the NULL-return MemoryError names the function that actually
        # returned NULL, not a <comp>_create that may not exist.
        ctx = Ctx.make_errors_ctx("widget", create_fn="widget_forge")
        assert "widget_forge returned NULL" in ctx["create_fail_block"]

    def test_error_message_default_byte_identical(self):
        # Default (no override) preserves the historical text exactly.
        assert (
            "widget_create returned NULL"
            in Ctx.make_errors_ctx("widget")["create_fail_block"]
        )


# ── end-to-end: scaffold, regenerate, status ────────────────────────────────


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "proj"
    new_run("proj", dest, [], [])
    module_run(dest, "eng")
    with contextlib.redirect_stdout(io.StringIO()):
        object_run(
            dest,
            "widget",
            "eng",
            None,
            no_state=True,
            no_step=True,
            init_params=[
                ("gain", "double", "1.0", "", "", "", False, "", False)
            ],
            create_fn="widget_forge",
        )
    return dest


def _frag(root):
    return (root / "native" / "src" / "eng" / "eng_ext_widget.c").read_text(
        encoding="utf-8"
    )


class TestScaffold:
    def test_create_fn_persisted(self, project):
        cfg = C.load(project)
        assert C.object_create_fn(cfg, "widget") == "widget_forge"

    def test_generated_calls_override(self, project):
        frag = _frag(project)
        assert "widget_forge(" in frag
        assert "widget_forge returned NULL" in frag


class TestSurvivesRegeneration:
    """The whole point: delete-the-fragment-and-apply must restore the
    override from the manifest alone — no hand-patch to drop."""

    def test_apply_restores_override(self, project):
        frag_path = project / "native" / "src" / "eng" / "eng_ext_widget.c"
        frag_path.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(project)
        frag = frag_path.read_text(encoding="utf-8")
        assert "widget_forge(" in frag
        assert "widget_create(" not in frag

    def test_status_clean(self, project):
        rc = status_run(project, check=True)
        assert rc == 0

    def test_script_roundtrips_create_fn(self, project):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            script_run(project)
        assert "--create-fn widget_forge" in buf.getvalue()
