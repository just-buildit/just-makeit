"""gh-446: a standalone object's `.pyi` never gets a newly added property.

`_property.py::run()` updated `_core.h`/`_ext.c` for a standalone (non-
module) object but never touched the `.pyi` at all — unlike the module-
owned path, which rebuilds its aggregated `.pyi` via `_stubs.make_module_pyi`
on every `jm property` call. Because `jm apply`'s replay and `jm status
--check` both materialize properties by calling this same `_property.run()`
internally, the gap was invisible on *three* fronts: immediately after `jm
property`, after a follow-up `jm apply`, and to `jm status --check` itself
(which reported clean because its own scratch-copy replay hit the identical
gap and thus saw no diff).

The deeper cause: even a direct `.pyi` write wasn't enough on its own — the
standalone `component.pyi` template's getter/setter stub placeholder
(`getter_setter_stubs_pyi`) is populated purely from state-variable get_x()/
set_x() *methods* (make_state_ctx), which has no notion of a manifest
`[[obj.properties]]` entry at all. A manifest property is a PyGetSetDef-
backed Python `@property` descriptor, not a get/set method pair, and had no
template placeholder of its own. Fixed by adding a `property_stubs_pyi`
context key (make_properties_ctx) and wiring it into the template and every
site that renders it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._property import run as property_run
from just_makeit._apply import run as apply_run
from just_makeit import _config as C
from just_makeit import _status


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["fft"])
    return dest


def _pyi(project: Path) -> str:
    return (project / "src" / "dsp" / "fft.pyi").read_text(encoding="utf-8")


class TestPropertyImmediatelyUpdatesPyi:
    def test_writable_property_gets_getter_and_setter_stub(self, project):
        property_run(project, "fft", "scale", None, "float", True)
        pyi = _pyi(project)
        assert "    @property" in pyi
        assert "    def scale(self) -> float:" in pyi
        assert "    @scale.setter" in pyi
        assert "    def scale(self, value: float) -> None: ..." in pyi

    def test_readonly_property_gets_no_setter_stub(self, project):
        property_run(project, "fft", "dropped", None, "size_t", False)
        pyi = _pyi(project)
        assert "    def dropped(self) -> int:" in pyi
        assert "@dropped.setter" not in pyi

    def test_multiple_properties_all_present(self, project):
        property_run(project, "fft", "scale", None, "float", True)
        property_run(project, "fft", "dropped", None, "size_t", False)
        pyi = _pyi(project)
        assert "def scale(self)" in pyi
        assert "def dropped(self)" in pyi

    def test_property_survives_later_method_regen(self, project):
        from just_makeit._method import run as method_run

        property_run(project, "fft", "scale", None, "float", True)
        method_run(
            project,
            "fft",
            "execute_ctrl",
            None,
            "float _Complex",
            "size_t",
            False,
            [],
        )
        pyi = _pyi(project)
        assert "def scale(self)" in pyi
        assert "def execute_ctrl(self" in pyi


class TestApplyMaterializesProperty:
    def test_apply_after_manifest_only_property_add(self, project):
        # Simulates the "silent" half of the bug: something (a hand edit,
        # a merge) added the property straight to the manifest without
        # going through `jm property`, so the .pyi never saw it.
        cfg = C.load(project)
        C.add_property(cfg, "fft", {"name": "scale", "type": "float"})
        C.save(project, cfg)
        assert "scale" not in _pyi(project)

        apply_run(project)
        assert "def scale(self)" in _pyi(project)

    def test_apply_is_idempotent_after_property_add(self, project):
        property_run(project, "fft", "scale", None, "float", True)
        before = _pyi(project)
        apply_run(project)
        assert _pyi(project) == before


class TestStatusDetectsPropertyDrift:
    def test_status_flags_stale_pyi_missing_property(self, project, capsys):
        cfg = C.load(project)
        C.add_property(cfg, "fft", {"name": "scale", "type": "float"})
        C.save(project, cfg)

        rc = _status.run(project, check=True)
        out = capsys.readouterr().out
        assert rc != 0
        assert "STALE" in out or "stale" in out.lower()

    def test_status_clean_after_apply(self, project, capsys):
        cfg = C.load(project)
        C.add_property(cfg, "fft", {"name": "scale", "type": "float"})
        C.save(project, cfg)
        apply_run(project)

        rc = _status.run(project, check=True)
        assert rc == 0
