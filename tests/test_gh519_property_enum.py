"""gh-519: an object property silently ignored ``enum = "<name>"``.

A property could declare ``enum = "ftype"`` and jm would happily accept it
and then emit ``PyLong_FromLong((long)self->handle->file_type)`` — the raw
SSOT int. That mattered because migrating a ``kind="handle"`` type (where
``enum`` has always been honoured) to an object turned every enum string in
the public API into an int, with no error and no warning: a silent break.

The fix teaches ``make_properties_ctx`` the ``[[enum]]`` registry. The C side
still stores the int; only the Python face becomes the ordered string:

  getter  -> ``PyUnicode_FromString(_enum_<Component>_<name>[<acc>])``
  setter  -> parse ``"s"``, resolve via ``_enum_index_<Component>``, raise
             ValueError listing the choices when it is not a member
  stub    -> ``Literal["a", "b", ...]`` instead of ``int``

The tables are namespaced by ``Component`` rather than emitted under the bare
``_enum_index`` / ``_enum_<name>`` names the composer, handle and module
``function`` generators already use. A module aggregator ``#include``s every
object's *and* every view's fragment into one translation unit, so two types
declaring a property on the same enum — or one object plus a module-level
enum function — would otherwise collide on duplicate static definitions.
``Component`` is the one name guaranteed unique per type section (it already
namespaces every getter and setter there).
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._context import make_properties_ctx
from just_makeit._new import run as new_run
from just_makeit._property import run as property_run

ENUMS = {"ftype": ["raw", "wav", "blue"]}


def _ctx(props, **kw):
    return make_properties_ctx("rdr", "Rdr", props, **kw)


# ── getter decode ────────────────────────────────────────────────────────────


class TestGetterDecode:
    def test_field_property_decodes_through_the_table(self):
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}],
            enums=ENUMS,
        )
        assert "long _v = (long)(self->handle->ft);" in ctx["getset_def"]
        assert (
            "return PyUnicode_FromString(_enum_Rdr_ftype[_v]);"
            in ctx["getset_def"]
        )
        assert "PyLong_FromLong" not in ctx["getset_def"]

    def test_expr_property_decodes_through_the_table(self):
        ctx = _ctx(
            [
                {
                    "name": "ft",
                    "type": "int",
                    "expr": "self->handle->raw_kind",
                    "enum": "ftype",
                }
            ],
            enums=ENUMS,
        )
        assert "long _v = (long)(self->handle->raw_kind);" in ctx["getset_def"]
        assert (
            "return PyUnicode_FromString(_enum_Rdr_ftype[_v]);"
            in ctx["getset_def"]
        )

    def test_c_getter_property_evaluates_the_call_once(self):
        """The default shape's accessor is a function call; it is bound into
        the range-check local so it is not evaluated twice (once for the
        bounds test and again inside the subscript)."""
        ctx = _ctx(
            [{"name": "ft", "type": "int", "enum": "ftype"}], enums=ENUMS
        )
        body = ctx["getset_def"]
        assert "long _v = (long)(rdr_get_ft(self->handle));" in body
        assert body.count("rdr_get_ft(self->handle)") == 1
        assert "return PyUnicode_FromString(_enum_Rdr_ftype[_v]);" in body

    def test_decode_is_range_checked_before_indexing(self):
        """gh-519: C owns the stored value — typically decoded from an
        external source such as a file header — so an unknown code is
        reachable input, not an internal invariant.

        Indexing the table blind read past its end (at exactly ``len`` that is
        the NULL terminator, giving ``PyUnicode_FromString(NULL)``), which in
        practice surfaced as a garbage string, a UnicodeDecodeError, or a
        crash depending on what followed in memory.
        """
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}],
            enums=ENUMS,
        )
        body = ctx["getset_def"]
        # ftype has 3 choices, so the valid index range is 0..2.
        assert "if (_v < 0 || _v >= 3) {" in body
        assert "PyErr_Format(PyExc_ValueError," in body
        assert "out-of-range ftype value" in body
        # The check must precede the subscript, not follow it.
        assert body.index("if (_v < 0 || _v >= 3)") < body.index(
            "_enum_Rdr_ftype[_v]"
        )

    def test_tables_precede_the_getters_that_index_them(self):
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}],
            enums=ENUMS,
        )
        body = ctx["getset_def"]
        assert body.index(
            "static const char *const _enum_Rdr_ftype[]"
        ) < body.index("Rdr_getprop_ft")

    def test_table_order_is_the_c_int(self):
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}],
            enums=ENUMS,
        )
        table = ctx["getset_def"].split("_enum_Rdr_ftype[] = {")[1]
        table = table.split("};")[0]
        assert (
            table.index('"raw"') < table.index('"wav"') < table.index('"blue"')
        )
        assert "NULL," in table

    def test_only_referenced_enums_are_emitted(self):
        registry = {"ftype": ["raw"], "unused": ["a", "b"]}
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}],
            enums=registry,
        )
        assert "_enum_Rdr_ftype" in ctx["getset_def"]
        assert "_enum_Rdr_unused" not in ctx["getset_def"]

    def test_two_properties_on_one_enum_emit_one_table(self):
        ctx = _ctx(
            [
                {"name": "a", "type": "int", "field": True, "enum": "ftype"},
                {"name": "b", "type": "int", "field": True, "enum": "ftype"},
            ],
            enums=ENUMS,
        )
        body = ctx["getset_def"]
        assert body.count("static const char *const _enum_Rdr_ftype[]") == 1
        assert body.count("_enum_index_Rdr(const char") == 1


# ── setter encode + errors ───────────────────────────────────────────────────


class TestSetterEncode:
    @pytest.fixture()
    def setter(self):
        return _ctx(
            [
                {
                    "name": "ft",
                    "type": "int",
                    "field": True,
                    "writable": True,
                    "enum": "ftype",
                }
            ],
            enums=ENUMS,
        )["getset_def"]

    def test_parses_a_string_not_an_int(self, setter):
        assert 'PyArg_Parse(value, "s", &v_str)' in setter
        assert 'PyArg_Parse(value, "i"' not in setter

    def test_resolves_through_the_namespaced_index_fn(self, setter):
        assert "_enum_index_Rdr(_enum_Rdr_ftype, v_str)" in setter

    def test_unknown_choice_raises_value_error_listing_choices(self, setter):
        assert "if (v_idx < 0) {" in setter
        assert "PyErr_Format(PyExc_ValueError," in setter
        assert "invalid ft '%s' (choices: raw, wav, blue)" in setter
        assert "        return -1;" in setter

    def test_assigns_the_resolved_int_where_v_would_have_gone(self, setter):
        assert "    int v = (int)v_idx;" in setter
        assert "    self->handle->ft = v;" in setter

    def test_c_setter_shape_also_assigns_the_resolved_int(self):
        body = _ctx(
            [
                {
                    "name": "ft",
                    "type": "int",
                    "writable": True,
                    "enum": "ftype",
                }
            ],
            enums=ENUMS,
        )["getset_def"]
        assert "rdr_set_ft(self->handle, v);" in body


# ── generation-time diagnostics ──────────────────────────────────────────────


class TestDiagnostics:
    def test_unknown_enum_name_raises_naming_everything(self):
        with pytest.raises(ValueError) as exc:
            _ctx(
                [
                    {
                        "name": "ft",
                        "type": "int",
                        "field": True,
                        "enum": "ftpye",
                    }
                ],
                enums=ENUMS,
            )
        msg = str(exc.value)
        assert "rdr.ft" in msg
        assert "ftpye" in msg
        assert "ftype" in msg

    def test_buf_field_with_enum_is_rejected(self):
        with pytest.raises(ValueError) as exc:
            _ctx(
                [
                    {
                        "name": "buf",
                        "type": "float[]",
                        "buf_field": "data",
                        "enum": "ftype",
                    }
                ],
                enums=ENUMS,
            )
        msg = str(exc.value)
        assert "rdr.buf" in msg
        assert "buf_field" in msg

    def test_none_registry_never_crashes_on_a_declared_enum(self):
        """The `jm bind` path has no manifest, so it passes no registry. A
        property carrying `enum` there must be inert, not fatal."""
        ctx = _ctx(
            [{"name": "ft", "type": "int", "field": True, "enum": "ftype"}]
        )
        assert "_enum_" not in ctx["getset_def"]
        assert "PyLong_FromLong" in ctx["getset_def"]


# ── the no-enum render is untouched ──────────────────────────────────────────


class TestNoEnumIsByteIdentical:
    """Item 1 of the gh-519 contract: passing a registry must change nothing
    for a component whose properties do not reference one. Pinning it here
    means the whole pre-gh-519 property surface cannot drift as a side
    effect of the enum path."""

    PROPS = [
        {"name": "gain", "type": "double", "field": True, "writable": True},
        {"name": "n", "type": "size_t"},
        {
            "name": "buf",
            "type": "float[]",
            "buf_field": "data",
            "len_field": "n",
        },
        {"name": "ratio", "type": "double", "expr": "self->handle->a / 2.0"},
    ]

    def test_registry_makes_no_difference(self):
        without = _ctx(self.PROPS)
        with_reg = _ctx(self.PROPS, enums=ENUMS)
        assert without == with_reg

    def test_pyi_typing_slot_stays_empty(self):
        assert _ctx(self.PROPS, enums=ENUMS)["pyi_property_typing"] == ""

    def test_empty_property_list_supplies_the_new_slot(self):
        assert (
            make_properties_ctx("rdr", "Rdr", [])["pyi_property_typing"] == ""
        )


# ── .pyi stub ────────────────────────────────────────────────────────────────


class TestStub:
    def test_property_annotates_as_literal_both_ways(self):
        ctx = _ctx(
            [
                {
                    "name": "ft",
                    "type": "int",
                    "field": True,
                    "writable": True,
                    "enum": "ftype",
                }
            ],
            enums=ENUMS,
        )
        pyi = ctx["property_stubs_pyi"]
        lit = 'Literal["raw", "wav", "blue"]'
        assert f"    def ft(self) -> {lit}:" in pyi
        assert f"    def ft(self, value: {lit}) -> None: ..." in pyi
        assert ctx["pyi_property_typing"] == ", Literal"


# ── end to end ───────────────────────────────────────────────────────────────


def _write_enum(root: Path, name="ftype", values=("raw", "wav", "blue")):
    """Append a top-level [[enum]] to the manifest (TOML-only, like every
    other consumer of the SSOT)."""
    vals = ", ".join(f'"{v}"' for v in values)
    with (root / C.FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(f'\n[[enum]]\nname = "{name}"\nvalues = [{vals}]\n')


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "probe"
    new_run("probe", dest, ["rdr"])
    _write_enum(dest)
    return dest


class TestEndToEnd:
    def test_cli_records_the_enum_and_renders_the_decode(self, project):
        property_run(
            project,
            "rdr",
            "ft",
            None,
            "int",
            True,
            field=True,
            enum="ftype",
        )
        cfg = C.load(project)
        assert C.properties(cfg, "rdr")[0]["enum"] == "ftype"
        ext = (project / "native" / "src" / "rdr" / "rdr_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_enum_Rdr_ftype" in ext
        assert "PyUnicode_FromString(_enum_Rdr_ftype[" in ext

    def test_out_of_range_value_raises_instead_of_reading_past_the_table(
        self, project
    ):
        """The emitted C must refuse an index the table cannot hold.

        Before the range check this compiled to a bare
        ``_enum_Rdr_ftype[self->handle->ft]``, so a value the C side decoded
        from an external source (a file header's format code, say) read past
        the table — returning adjacent memory as a string, raising
        UnicodeDecodeError, or crashing, depending on what followed it.
        """
        property_run(
            project, "rdr", "ft", None, "int", True, field=True, enum="ftype"
        )
        ext = (project / "native" / "src" / "rdr" / "rdr_ext.c").read_text(
            encoding="utf-8"
        )
        assert "if (_v < 0 || _v >= 3) {" in ext
        assert "out-of-range ftype value" in ext
        # Guard precedes the subscript.
        assert ext.index("if (_v < 0 || _v >= 3)") < ext.index(
            "_enum_Rdr_ftype[_v]"
        )

    def test_generated_pyi_parses_and_imports_literal(self, project):
        property_run(
            project, "rdr", "ft", None, "int", True, field=True, enum="ftype"
        )
        pyi_path = project / "src" / "probe" / "rdr.pyi"
        text = pyi_path.read_text(encoding="utf-8")
        ast.parse(text)  # a stub that does not parse is not a stub
        assert "from typing import Any, final, Literal" in text
        assert 'Literal["raw", "wav", "blue"]' in text

    def test_apply_replays_the_enum_and_is_idempotent(self, project):
        property_run(
            project, "rdr", "ft", None, "int", True, field=True, enum="ftype"
        )
        ext_path = project / "native" / "src" / "rdr" / "rdr_ext.c"
        first = ext_path.read_text(encoding="utf-8")
        apply_run(project)
        assert ext_path.read_text(encoding="utf-8") == first
        apply_run(project)
        assert ext_path.read_text(encoding="utf-8") == first
        assert "_enum_Rdr_ftype" in first

    def test_script_round_trips_the_enum_flag(self, project):
        property_run(
            project, "rdr", "ft", None, "int", True, field=True, enum="ftype"
        )
        from just_makeit._script import _property_flags

        prop = C.properties(C.load(project), "rdr")[0]
        flags = "".join(_property_flags(prop, None))
        assert "--enum ftype" in flags

    def test_unknown_enum_from_the_cli_exits_without_writing(self, project):
        before = (project / C.FILENAME).read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            property_run(
                project,
                "rdr",
                "ft",
                None,
                "int",
                True,
                field=True,
                enum="ftpye",
            )
        assert exc.value.code == 1
        assert (project / C.FILENAME).read_text(encoding="utf-8") == before


# ── the duplicate-symbol case ────────────────────────────────────────────────


class TestModuleSharedEnum:
    """The highest-risk part of gh-519.

    ``<module>_ext.c`` ``#include``s each object's ``<module>_ext_<id>.c``
    fragment, so every object *and* every view in a module lands in a single
    translation unit. Two of them declaring a property on the same enum would
    define ``_enum_index`` and ``_enum_ftype`` twice and fail to compile.
    """

    @pytest.fixture()
    def module_project(self, tmp_path):
        dest = tmp_path / "mprobe"
        new_run("mprobe", dest, [])
        _write_enum(dest)
        cfg = C.load(dest)
        with (dest / C.FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(
                '\n[module.io]\nobjects = ["rdr", "wtr"]\n'
                '\n[rdr]\nclass_name = "Rdr"\n'
                '\n[[rdr.state]]\nname = "ft"\ntype = "int"\ndefault = "0"\n'
                "\n[[rdr.properties]]\n"
                'name = "ft"\ntype = "int"\nfield = true\n'
                'writable = true\nenum = "ftype"\n'
                "\n[[rdr.views]]\n"
                'class_name = "RdrView"\ncreate_fn = "rdr_create_view"\n'
                '\n[wtr]\nclass_name = "Wtr"\n'
                '\n[[wtr.state]]\nname = "ft"\ntype = "int"\ndefault = "1"\n'
                "\n[[wtr.properties]]\n"
                'name = "ft"\ntype = "int"\nfield = true\n'
                'writable = true\nenum = "ftype"\n'
            )
        assert cfg is not None
        apply_run(dest)
        return dest

    def test_each_type_owns_a_distinct_symbol_namespace(self, module_project):
        src = module_project / "native" / "src" / "io"
        blob = "".join(
            p.read_text(encoding="utf-8") for p in sorted(src.glob("*.c"))
        )
        # One index fn + one table per *type*, never a bare shared symbol.
        for comp in ("Rdr", "RdrView", "Wtr"):
            assert blob.count(f"_enum_index_{comp}(const char") == 1
            assert (
                blob.count(f"static const char *const _enum_{comp}_ftype[]")
                == 1
            )
        assert "_enum_index(const char" not in blob
        assert "static const char *const _enum_ftype[]" not in blob

    def test_the_aggregated_translation_unit_compiles(self, module_project):
        """A symbol clash is a *link/compile* failure, so only a real compile
        proves it away. Preprocess-and-compile the aggregator exactly as the
        build does."""
        import sysconfig

        try:
            import numpy
        except ImportError:  # pragma: no cover - deps fixture absent
            pytest.skip("numpy headers unavailable")
        cc = sysconfig.get_config_var("CC") or "cc"
        agg = module_project / "native" / "src" / "io" / "io_ext.c"
        cmd = cc.split() + [
            "-c",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror=implicit-function-declaration",
            f"-I{module_project / 'native' / 'inc'}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{numpy.get_include()}",
            str(agg),
            "-o",
            str(module_project / "io_ext.o"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert "redefinition" not in res.stderr

    def test_module_pyi_imports_literal_and_parses(self, module_project):
        pyi = module_project / "src" / "mprobe" / "io" / "io.pyi"
        text = pyi.read_text(encoding="utf-8")
        ast.parse(text)
        assert "from typing import final, Literal" in text
        assert text.count('Literal["raw", "wav", "blue"]') >= 4
