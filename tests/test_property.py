"""Integration tests for `just-makeit property`."""

import re
import sys
from pathlib import Path

import pytest

# Generated stubs intentionally embed <<IMPLEMENT:...>> guidance comments.
# Only flag tokens that are NOT the IMPLEMENT marker.
_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._config import load, properties


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["buf"], [("capacity", "size_t", "1024")])
    return dest


class TestPropertyUpdatesExtC:
    def test_ext_c_has_getset_def(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyGetSetDef Buf_getset[]" in ext

    def test_ext_c_has_tp_getset(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert ".tp_getset" in ext
        assert "Buf_getset" in ext

    def test_ext_c_getter_calls_correct_fn(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "buf_get_dropped(self->handle)" in ext

    def test_ext_c_getter_stub_signature(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Buf_getprop_dropped" in ext

    def test_ext_c_getset_entry_with_null_setter(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"dropped"' in ext
        # read-only: setter should be NULL
        assert "NULL, NULL" in ext

    def test_ext_c_getset_null_terminator(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "{ NULL }" in ext

    def test_ext_c_multiple_properties(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        property_run(project, "buf", "available", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Buf_getprop_dropped" in ext
        assert "Buf_getprop_available" in ext
        # Only one getset array
        assert ext.count("PyGetSetDef Buf_getset[]") == 1

    def test_ext_c_writable_has_setter(self, project):
        property_run(project, "buf", "threshold", None, "size_t", True)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Buf_setprop_threshold" in ext
        assert "buf_set_threshold(self->handle" in ext

    def test_ext_c_readonly_no_setter_fn(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Buf_setprop_dropped" not in ext


class TestPropertyUpdatesCoreH:
    def test_core_h_has_getter_decl(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "buf_get_dropped" in h

    def test_core_h_has_setter_decl_when_writable(self, project):
        property_run(project, "buf", "threshold", None, "size_t", True)
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "buf_set_threshold" in h

    def test_core_h_no_setter_decl_when_readonly(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "buf_set_dropped" not in h


class TestPropertyUpdatesConfig:
    def test_config_records_property(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        cfg = load(project)
        names = [p["name"] for p in properties(cfg, "buf")]
        assert "dropped" in names

    def test_config_records_type(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        cfg = load(project)
        p = next(p for p in properties(cfg, "buf") if p["name"] == "dropped")
        assert p["type"] == "size_t"

    def test_config_readonly_no_writable_flag(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        cfg = load(project)
        p = next(p for p in properties(cfg, "buf") if p["name"] == "dropped")
        assert not p.get("writable", False)

    def test_config_records_writable(self, project):
        property_run(project, "buf", "threshold", None, "size_t", True)
        cfg = load(project)
        p = next(p for p in properties(cfg, "buf") if p["name"] == "threshold")
        assert p.get("writable") is True

    def test_config_multiple_properties(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        property_run(project, "buf", "available", None, "size_t", False)
        cfg = load(project)
        names = [p["name"] for p in properties(cfg, "buf")]
        assert "dropped" in names
        assert "available" in names


class TestPropertyValidation:
    def test_no_config_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            property_run(tmp_path, "buf", "dropped", None, "size_t", False)

    def test_unknown_object_exits(self, project):
        with pytest.raises(SystemExit):
            property_run(
                project, "nonexistent", "dropped", None, "size_t", False
            )

    def test_unsupported_type_exits(self, project):
        with pytest.raises(SystemExit):
            property_run(
                project, "buf", "dropped", None, "notavalidtype", False
            )

    def test_duplicate_property_name_exits(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        with pytest.raises(SystemExit):
            property_run(project, "buf", "dropped", None, "size_t", False)


def _check_no_placeholders(project: Path) -> None:
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (
            ".py",
            ".c",
            ".h",
            ".toml",
            ".txt",
        ):
            text = path.read_text(encoding="utf-8")
            m = _STRAY_PLACEHOLDER.search(text)
            assert m is None, f"Unreplaced placeholder in {path}"


class TestPropertyNoUnreplacedPlaceholders:
    def test_no_placeholders_readonly(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        _check_no_placeholders(project)

    def test_no_placeholders_writable(self, project):
        property_run(project, "buf", "threshold", None, "size_t", True)
        _check_no_placeholders(project)

    def test_no_placeholders_multiple_props(self, project):
        property_run(project, "buf", "dropped", None, "size_t", False)
        property_run(project, "buf", "threshold", None, "size_t", True)
        _check_no_placeholders(project)

    def test_no_placeholders_field(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        _check_no_placeholders(project)


class TestPropertyField:
    """--field adds struct field + auto-implements getter/setter."""

    def test_struct_field_in_core_h(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", False, field=True
        )
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint32_t phase;" in h

    def test_struct_field_not_in_create_params(self, project):
        """Field-backed property must NOT appear as a constructor parameter."""
        property_run(
            project, "buf", "phase", None, "uint32_t", False, field=True
        )
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "buf_create(size_t capacity)" in h

    def test_getter_uses_handle_field(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", False, field=True
        )
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle->phase" in ext

    def test_getter_no_implement_comment(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", False, field=True
        )
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        # Should not have <<IMPLEMENT>> in the getter for this property
        assert "IMPLEMENT" not in ext

    def test_writable_setter_assigns_field(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle->phase = v;" in ext

    def test_no_extern_decl_in_core_h(self, project):
        """Field-backed property must not add buf_get_phase / buf_set_phase decls."""
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "buf_get_phase" not in h
        assert "buf_set_phase" not in h

    def test_config_stores_field_flag(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        cfg = load(project)
        p = next(p for p in properties(cfg, "buf") if p["name"] == "phase")
        assert p.get("field") is True

    def test_multiple_field_props(self, project):
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        property_run(
            project, "buf", "phase_inc", None, "uint32_t", False, field=True
        )
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint32_t phase;" in h
        assert "uint32_t phase_inc;" in h

    def test_field_mixed_with_computed(self, project):
        """Field-backed and computed properties coexist correctly."""
        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        property_run(project, "buf", "status", None, "uint32_t", False)
        ext = (project / "native" / "src" / "buf" / "buf_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle->phase" in ext
        assert "buf_get_status(self->handle)" in ext
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint32_t phase;" in h
        assert "buf_get_status" in h

    def test_field_survives_add(self, project):
        """Struct field must still be present after just-makeit add."""
        from just_makeit._add import run as add_run

        property_run(
            project, "buf", "phase", None, "uint32_t", True, field=True
        )
        add_run(project, "buf", [("gain", "float", "1.0f")])
        h = (project / "native" / "inc" / "buf" / "buf_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint32_t phase;" in h
        assert "float gain;" in h


class TestPropertyFieldModule:
    """--field on a module object must update _core.h, not just _ext.c."""

    @pytest.fixture()
    def mod_project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, modules=["sig"])
        object_run(dest, "nco", "sig", [("freq", "float", "440.0f")])
        return dest

    def test_struct_field_written_to_core_h(self, mod_project):
        property_run(
            mod_project, "nco", "phase", "sig", "uint32_t", False, field=True
        )
        h = (mod_project / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint32_t phase;" in h

    def test_ext_c_uses_handle_field(self, mod_project):
        property_run(
            mod_project, "nco", "phase", "sig", "uint32_t", False, field=True
        )
        ext = (mod_project / "native" / "src" / "sig" / "sig_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle->phase" in ext

    def test_writable_setter_in_ext_c(self, mod_project):
        property_run(
            mod_project, "nco", "phase", "sig", "uint32_t", True, field=True
        )
        ext = (mod_project / "native" / "src" / "sig" / "sig_ext.c").read_text(
            encoding="utf-8"
        )
        assert "self->handle->phase = v;" in ext

    def test_no_extern_decl_in_core_h(self, mod_project):
        property_run(
            mod_project, "nco", "phase", "sig", "uint32_t", True, field=True
        )
        h = (mod_project / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "nco_get_phase" not in h
        assert "nco_set_phase" not in h

    def test_field_keeps_init_param_in_create_signature(self, tmp_path):
        """--field regen must not drop init_params from the create() signature.

        Regression for gh#4: ``property --field`` on an object that has
        ``init_params`` regenerated ``_core.h`` without passing the params
        through, reverting the header to ``create(void)`` while the impl
        kept ``create(<params>)`` — a conflicting-types compile error.
        """
        dest = tmp_path / "dsp"
        new_run("dsp", dest, modules=["core"])
        object_run(
            dest,
            "counter",
            "core",
            no_state=True,
            no_step=True,
            init_params=[("count", "uint64_t", "0")],
        )
        property_run(
            dest, "counter", "count", "core", "uint64_t", True, field=True
        )
        h = (dest / "native" / "inc" / "counter" / "counter_core.h").read_text(
            encoding="utf-8"
        )
        c = (dest / "native" / "src" / "counter" / "counter_core.c").read_text(
            encoding="utf-8"
        )
        assert "counter_create(uint64_t count);" in h
        assert "counter_create(void)" not in h
        assert "counter_create(uint64_t count)" in c
