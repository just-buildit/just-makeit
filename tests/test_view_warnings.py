"""Tests for view-level warnings — ``[[<obj>.views.warnings]]`` (gh-509).

A view (gh-504) carries no parent warnings, so before this a second front door
over one core (doppler's ``BurstAcquisition``) could not surface its own
``PyErr_WarnEx`` (the under-powered-search notice) without a hand-patch that
regeneration drops. The load-bearing test is ``TestSurvivesRegeneration``: the
view's warning rebuilds from the manifest alone and ``jm status --check`` stays
clean.
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
from just_makeit._view import run as view_run
from just_makeit._warning import run as warning_run
from just_makeit._apply import run as apply_run
from just_makeit._script import run as script_run
from just_makeit._status import run as status_run


# ── config layer ────────────────────────────────────────────────────────────


def _view_with_warning():
    return {
        "class_name": "Gadget",
        "create_fn": "widget_forge_gadget",
        "warnings": [
            {
                "after": "__init__",
                "condition": "ready",
                "category": "UserWarning",
                "message": "gadget not ready",
            }
        ],
    }


class TestConfigLayer:
    def test_accessor(self):
        assert C.view_warnings(_view_with_warning())[0]["condition"] == "ready"

    def test_accessor_default_empty(self):
        assert C.view_warnings({"class_name": "X", "create_fn": "x"}) == []

    def test_add_view_warning_idempotent(self):
        cfg = {
            "widget": {"views": [{"class_name": "Gadget", "create_fn": "wf"}]}
        }
        w = {"after": "__init__", "condition": "ready", "message": "a"}
        C.add_view_warning(cfg, "widget", "Gadget", w)
        C.add_view_warning(
            cfg, "widget", "Gadget", {**w, "message": "updated"}
        )
        got = C.view_warnings(cfg["widget"]["views"][0])
        assert len(got) == 1 and got[0]["message"] == "updated"

    def test_dump_reload_roundtrip(self):
        cfg = {
            "project": {"name": "demo", "version": "0.1.0"},
            "widget": {
                "arg_type": "float _Complex",
                "return_type": "float _Complex",
                "state": [],
                "views": [_view_with_warning()],
            },
        }
        reloaded = tomllib.loads(C._dump(cfg))
        v = C.views(reloaded, "widget")[0]
        assert C.view_warnings(v)[0]["condition"] == "ready"
        assert C.view_warnings(v)[0]["message"] == "gadget not ready"


class TestGeneration:
    def test_view_ctx_emits_warn_block(self):
        # A view's make_warnings_ctx fires off its OWN list; the block guards a
        # bool field on the shared handle, exactly like a parent's.
        ctx = Ctx.make_warnings_ctx(
            "widget",
            "Gadget",
            C.view_warnings(_view_with_warning()),
        )
        assert "self->handle->ready" in ctx["init_warn_block"]
        assert "gadget not ready" in ctx["init_warn_block"]


# ── end-to-end ──────────────────────────────────────────────────────────────


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
            [("ready", "int", "0")],
            no_step=True,
        )
        view_run(
            dest,
            "widget",
            "Gadget",
            "eng",
            "widget_forge_gadget",
        )
        warning_run(
            dest,
            "widget",
            "ready",
            "gadget not ready",
            module="eng",
            view="Gadget",
        )
    return dest


def _frag(root):
    return (root / "native" / "src" / "eng" / "eng_ext_gadget.c").read_text(
        encoding="utf-8"
    )


class TestScaffold:
    def test_warning_persisted_on_view(self, project):
        v = C.views(C.load(project), "widget")[0]
        assert C.view_warnings(v)[0]["condition"] == "ready"

    def test_generated_view_has_warn_block(self, project):
        frag = _frag(project)
        assert "PyErr_WarnEx" in frag
        assert "self->handle->ready" in frag
        assert "gadget not ready" in frag

    def test_parent_object_has_no_warning(self, project):
        # The warning is the view's alone — the object's own fragment stays bare.
        parent = (
            project / "native" / "src" / "eng" / "eng_ext_widget.c"
        ).read_text(encoding="utf-8")
        assert "gadget not ready" not in parent


class TestSurvivesRegeneration:
    def test_view_requires_module(self, tmp_path, capsys):
        dest = tmp_path / "p2"
        new_run("p2", dest, [], [])
        module_run(dest, "eng")
        with contextlib.redirect_stdout(io.StringIO()):
            object_run(
                dest, "widget", "eng", [("ready", "int", "0")], no_step=True
            )
            view_run(dest, "widget", "Gadget", "eng", "widget_forge_gadget")
        with pytest.raises(SystemExit):
            warning_run(dest, "widget", "ready", "x", view="Gadget")

    def test_apply_restores_view_warning(self, project):
        frag_path = project / "native" / "src" / "eng" / "eng_ext_gadget.c"
        frag_path.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(project)
        assert "gadget not ready" in frag_path.read_text(encoding="utf-8")

    def test_status_clean(self, project):
        assert status_run(project, check=True) == 0

    def test_script_roundtrips_view_warning(self, project):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            script_run(project)
        out = buf.getvalue()
        assert "warning widget" in out
        assert "--view Gadget" in out
