"""gh-1021: ``enum`` on an object METHOD parameter.

The key was already accepted by the manifest validator (`_keys.PARAM_KEYS`)
and read by nothing. A method parameter declaring ``type = "int"`` plus
``enum = "<name>"`` generated a plain ``int`` and said nothing, so a caller
following the manifest's own declaration got

    TypeError: 'str' object cannot be interpreted as an integer

and the second plausible spelling, ``type = "enum:<name>"`` (which is what an
init_param takes), raised a bare ``KeyError`` out of ``_CTYPE_META``. Of the
three ways to write it, one worked only on module functions, one only on
init_params, and neither reached a method — one silently, one as a traceback.

`_keys.FUNCTION_PARAM_KEYS` already carried the reasoning this issue is an
instance of: "recognising the key on only one side would accept it in the
manifest and drop it in the C."

The load-bearing tests are TestSharedTables and TestSlotOrder. gh-519 gave
PROPERTIES the same feature and emitted the lookup tables inside ``getset_def``
— the LAST of the three C slots that can reference them. A method parameter's
lookup lands in ``extra_methods_c``, one slot earlier, so the tables had to
move above all of them, and they must stay there.
"""

from __future__ import annotations

import io
import contextlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _render  # noqa: E402
from just_makeit._context import make_enum_tables_ctx  # noqa: E402
from just_makeit._context._methods import method_param_enums  # noqa: E402
from just_makeit._context._parse import (  # noqa: E402
    _build_params_parse,
    enum_symbols,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402

ENUMS = {"kindE": ["none", "rs", "conv"]}


def _method(**kw):
    p = {"name": "kind", "type": "int", "enum": "kindE"}
    p.update(kw)
    return [{"name": "add", "return_type": "int", "params": [p]}]


# ── the emitter ──────────────────────────────────────────────────────────────


class TestEmitter:
    def test_parses_a_string_not_an_int(self):
        block, call, _ = _build_params_parse(
            _method()[0]["params"], "Frame", ENUMS
        )
        assert 'const char *kind = "";' in block
        assert '"s"' in block or 's"' in block
        # ...and the C call gets the validated int, not the string.
        assert call == "_arg_kind"

    def test_validates_through_the_component_scoped_table(self):
        block, _, _ = _build_params_parse(
            _method()[0]["params"], "Frame", ENUMS
        )
        index_fn, table = enum_symbols("Frame", "kindE")
        assert f"int _arg_kind = {index_fn}({table}, kind);" in block

    def test_bad_choice_raises_valueerror_naming_the_choices(self):
        block, _, _ = _build_params_parse(
            _method()[0]["params"], "Frame", ENUMS
        )
        assert "if (_arg_kind < 0)" in block
        assert "PyExc_ValueError" in block
        # The PROPERTY setter for the same enum names its choices; a caller
        # who meets both refusals should not meet two styles of it.
        assert "(choices: none, rs, conv)" in block

    def test_a_default_seeds_the_choice_string(self):
        block, _, _ = _build_params_parse(
            _method(default="rs")[0]["params"], "Frame", ENUMS
        )
        assert 'const char *kind = "rs";' in block

    def test_no_registry_drops_the_choice_list_and_nothing_else(self):
        """The `jm bind` path has no manifest to read `[[enum]]` from."""
        block, _, _ = _build_params_parse(
            _method()[0]["params"], "Frame", None
        )
        assert "_enum_index_Frame(_enum_Frame_kindE, kind)" in block
        assert "choices:" not in block


class TestDeclarationErrors:
    def test_the_init_param_spelling_is_a_diagnostic_not_a_keyerror(self):
        with pytest.raises(ValueError) as e:
            _build_params_parse(
                [{"name": "kind", "type": "enum:kindE"}], "Frame", ENUMS
            )
        msg = str(e.value)
        assert "init_param spelling" in msg
        # It must name the spelling that DOES work here, or the reader is
        # left with a refusal and no next step.
        assert 'type = "int"' in msg
        assert 'enum = "kindE"' in msg

    def test_unknown_enum_names_the_known_ones(self):
        with pytest.raises(ValueError) as e:
            method_param_enums(_method(enum="nope"), ENUMS)
        assert "unknown enum 'nope'" in str(e.value)
        assert "kindE" in str(e.value)

    def test_an_int_default_on_an_enum_param_is_refused(self):
        """A manifest that kept the pre-enum int default would otherwise
        compile and raise `invalid kind '0'` on every call that omitted it."""
        with pytest.raises(ValueError) as e:
            method_param_enums(_method(default="0"), ENUMS)
        assert "default '0' is not a choice" in str(e.value)
        assert "none, rs, conv" in str(e.value)


# ── one table, both consumers ────────────────────────────────────────────────


class TestSharedTables:
    """A method parameter and a property may name the same `[[enum]]`.

    They index the same `_enum_<Component>_<name>` symbols, so a second
    emission is a duplicate definition in one TU, not a fallback.
    """

    PROP = [{"name": "mode", "type": "int", "field": True, "enum": "kindE"}]

    def test_method_only(self):
        t = make_enum_tables_ctx("frame", "Frame", _method(), [], enums=ENUMS)[
            "enum_tables"
        ]
        assert t.count("static const char *const _enum_Frame_kindE[]") == 1

    def test_property_only(self):
        t = make_enum_tables_ctx("frame", "Frame", [], self.PROP, enums=ENUMS)[
            "enum_tables"
        ]
        assert t.count("static const char *const _enum_Frame_kindE[]") == 1

    def test_both_emit_exactly_one_table_and_one_helper(self):
        t = make_enum_tables_ctx(
            "frame", "Frame", _method(), self.PROP, enums=ENUMS
        )["enum_tables"]
        assert t.count("static const char *const _enum_Frame_kindE[]") == 1
        assert t.count("_enum_index_Frame(const char") == 1

    def test_nothing_declared_renders_empty(self):
        """A type with no enum must render byte-identically to before."""
        assert (
            make_enum_tables_ctx("frame", "Frame", [], [], enums=ENUMS)[
                "enum_tables"
            ]
            == ""
        )


class TestSlotOrder:
    """The tables' slot must precede every slot that references them.

    This is the defect gh-1021 actually turned on: gh-519 put the tables in
    `getset_def`, and `extra_methods_c` — where a method parameter's lookup
    lands — is emitted BEFORE it. Asserted against the templates themselves
    so moving the slot back fails here rather than in a user's compiler.
    """

    CONSUMERS = (
        "getter_setter_methods_c",
        "extra_methods_c",
        "getset_def",
    )

    @pytest.mark.parametrize(
        "text",
        [
            (
                Path(_render.__file__).parent
                / "templates"
                / "c"
                / "src"
                / "component_ext.c"
            ).read_text(encoding="utf-8"),
            Path(_render.__file__).read_text(encoding="utf-8"),
        ],
        ids=["component_ext.c", "_render.py"],
    )
    def test_enum_tables_precedes_every_consumer(self, text):
        where = text.index("<<enum_tables>>")
        for slot in self.CONSUMERS:
            assert where < text.index(f"<<{slot}>>"), (
                f"<<enum_tables>> must precede <<{slot}>> — a table defined "
                f"after its use does not compile"
            )


# ── end to end ───────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", dest, modules=["wfm"])
        object_run(
            dest,
            "frame",
            module="wfm",
            arg_type="float _Complex",
            return_type="float _Complex",
            state_vars=[("n", "uint64_t", "0")],
        )
    cfg = C.load(dest)
    cfg.setdefault("enum", []).append(
        {"name": "kindE", "values": list(ENUMS["kindE"])}
    )
    cfg["frame"]["methods"] = _method(default="rs")
    C.save(dest, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(dest)
    return dest


def _frag(project):
    return (project / "native" / "src" / "wfm" / "wfm_ext_frame.c").read_text(
        encoding="utf-8"
    )


class TestEndToEnd:
    def test_the_table_is_defined_before_the_wrapper_uses_it(self, project):
        src = _frag(project)
        assert src.index(
            "static const char *const _enum_Frame_kindE[]"
        ) < src.index("Frame_add(")

    def test_both_faces_say_str(self, project):
        """The runtime docstring and the .pyi come from one builder (gh-642).

        A stub saying `int` is what handed the caller the TypeError this
        issue reports, and a docstring saying `int` documents the same lie.
        """
        assert "kind : str" in _frag(project)
        stub = (project / "src" / "proj" / "wfm" / "wfm.pyi").read_text(
            encoding="utf-8"
        )
        assert "kind: str = 'rs'" in stub

    def test_the_generated_example_is_a_real_choice(self, project):
        """The example is executable prose. A `0` there reads as a working
        call and is precisely the TypeError the issue reports."""
        example = re.search(r">>> obj\.add\(([^)]*)\)", _frag(project))
        assert example, "no generated example for the method"
        assert example.group(1) == "'rs'"
