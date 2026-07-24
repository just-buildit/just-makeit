"""End-to-end: rich Doxygen in _core.h -> derived Python docstrings.

Drives the real scaffold -> edit-header -> apply pipeline and asserts the
derived docstrings reach BOTH the generated ``.pyi`` (via _stubs) and the C
binding's PyMethodDef (via _context/_methods), with TOML override and a
no-Doxygen fallback.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402


_RICH_DOXYGEN = """\
  /**
   * @brief Scale the input sample by the configured gain.
   *
   * @param state  Must be non-NULL.
   * @param x      Sample to scale.
   * @return The scaled sample.
   */
"""


def _scaffold_with_method(dest: Path):
    new_run("dsp", dest)
    module_run(dest, "sig")
    object_run(
        dest,
        "mix",
        module="sig",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    method_run(
        dest,
        "mix",
        "scale",
        "sig",
        "float",
        "float",
        False,
        [],
    )


def _annotate(dest: Path, c_func: str, block: str):
    """Replace the Doxygen immediately above the *c_func* declaration."""
    header = dest / "native" / "inc" / "mix" / "mix_core.h"
    text = header.read_text(encoding="utf-8")
    # Strip only a Doxygen block IMMEDIATELY above the decl (the negative
    # lookahead keeps `.*?` from bridging across another block's `*/`), then
    # match the real declaration line — not a `@code` example inside a
    # comment (must not start with ` *`) nor an assignment (no `=`).
    decl_re = re.compile(
        r"(?:^[ \t]*/\*\*(?:(?!\*/)[\s\S])*?\*/[ \t]*\r?\n)?"
        r"(^(?![ \t]*[*/])[^\n=]*\b" + c_func + r"\s*\([^;]*\);)",
        re.MULTILINE,
    )
    text2 = decl_re.sub(block + r"\1", text, count=1)
    assert text2 != text, f"could not locate {c_func} declaration"
    header.write_text(text2, encoding="utf-8")


def _inject_rich_doxygen(dest: Path):
    """Replace mix_scale's template Doxygen with a rich hand-written block."""
    _annotate(dest, "mix_scale", _RICH_DOXYGEN)


class TestDerivedDocstrings:
    def test_brief_and_params_reach_pyi_and_c(self, tmp_path):
        dest = tmp_path / "dsp"
        _scaffold_with_method(dest)
        _inject_rich_doxygen(dest)
        apply_run(dest)

        pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
            encoding="utf-8"
        )
        assert "Scale the input sample by the configured gain." in pyi
        assert "Parameters" in pyi
        assert "Sample to scale." in pyi
        assert "The scaled sample." in pyi
        # C-only `state` param must not appear in the Python Parameters.
        scale_doc = pyi.split("def scale")[1].split('"""')[1]
        assert "state" not in scale_doc

    def test_c_pymethoddef_carries_derived_brief(self):
        """The C binding's PyMethodDef gets the @brief while keeping its
        synthesized doctest. (Tested at the generator level: an existing
        per-object _ext fragment is hand-owned and preserved by `apply`, so
        the fragment only picks this up when (re)generated.)"""
        from just_makeit._docstring import parse_doxygen_block
        from just_makeit._context._methods import make_methods_ctx

        blk = parse_doxygen_block(_RICH_DOXYGEN)
        ctx = make_methods_ctx(
            "mix",
            "Mix",
            [{"name": "scale", "arg_type": "float", "return_type": "float"}],
            pkg="dsp",
            doc_blocks={"mix_scale": blk},
        )
        pmd = ctx["extra_methods_pymethoddef"]
        assert "Scale the input sample by the configured gain." in pmd
        assert ">>> " in pmd  # synthesized doctest preserved

    def test_class_and_property_briefs_reach_pyi(self, tmp_path):
        dest = tmp_path / "dsp"
        _scaffold_with_method(dest)
        # explicit read-only property (state-var getters are get_X() methods,
        # not @property — only `jm property` emits a real property).
        property_run(dest, "mix", "level", "sig", "float", False)
        _annotate(
            dest,
            "mix_create",
            "/**\n * @brief A unity-gain sample scaler.\n */\n",
        )
        _annotate(
            dest,
            "mix_get_level",
            "  /**\n   * @brief The current output level in dBFS.\n   */\n",
        )
        apply_run(dest)
        pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
            encoding="utf-8"
        )
        # class docstring summary comes from create()'s @brief
        class_doc = pyi.split("class Mix:")[1].split('"""')[1]
        assert "A unity-gain sample scaler." in class_doc
        # property getter @brief becomes the property docstring
        level_doc = pyi.split("def level")[1].split('"""')[1]
        assert "The current output level in dBFS." in level_doc

    def test_standalone_class_summary_derives_from_create_brief(
        self, tmp_path
    ):
        """The STANDALONE .pyi class summary must enrich from the header's
        create() @brief, exactly like the module path above. The two .pyi
        generators previously drifted: the standalone template hardcoded
        ``"<Component> component."`` while the module aggregator derived it
        from the header. Both now route through _stubs.class_docstring_block,
        and the apply/status path (temp-scaffold replay had a trivial header)
        re-renders from the real header so the drift gate agrees."""
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(
            dest,
            "gain",
            module=None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        header = dest / "native" / "inc" / "gain" / "gain_core.h"
        text = header.read_text(encoding="utf-8")
        block = "/**\n * @brief A configurable scalar gain stage.\n */\n"
        decl_re = re.compile(
            r"(?:^[ \t]*/\*\*(?:(?!\*/)[\s\S])*?\*/[ \t]*\r?\n)?"
            r"(^(?![ \t]*[*/])[^\n=]*\bgain_create\s*\([^;]*\);)",
            re.MULTILINE,
        )
        text2 = decl_re.sub(block + r"\1", text, count=1)
        assert text2 != text, "could not locate gain_create declaration"
        header.write_text(text2, encoding="utf-8")

        apply_run(dest)

        pyi = (dest / "src" / "dsp" / "gain.pyi").read_text(encoding="utf-8")
        class_doc = pyi.split("class Gain:")[1].split('"""')[1]
        assert "A configurable scalar gain stage." in class_doc
        assert "<<" not in pyi  # no placeholder leak

    def test_property_getset_and_tp_doc_carry_brief(self):
        """C PyGetSetDef doc and tp_doc derive from the header (generator
        level — the per-object _ext fragment is preserved by apply)."""
        from just_makeit._docstring import parse_doxygen_block
        from just_makeit._context._methods import make_properties_ctx

        blk = parse_doxygen_block(
            "/** @brief The multiplicative gain applied per sample. */"
        )
        ctx = make_properties_ctx(
            "mix",
            "Mix",
            [{"name": "gain", "type": "float", "writable": True}],
            doc_blocks={"mix_get_gain": blk},
        )
        assert (
            "The multiplicative gain applied per sample." in ctx["getset_def"]
        )

    def test_no_doxygen_falls_back_to_name_stub(self, tmp_path):
        dest = tmp_path / "dsp"
        _scaffold_with_method(dest)
        # do NOT inject rich Doxygen — header keeps jm's trivial template
        apply_run(dest)
        pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
            encoding="utf-8"
        )
        # falls back to the name-based stub, no derived prose
        assert "Scale the input sample by the configured gain." not in pyi
        assert "def scale" in pyi

    def test_toml_doc_overrides_header_brief(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        module_run(dest, "sig")
        object_run(
            dest,
            "mix",
            module="sig",
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        # method created WITH an explicit TOML doc override
        method_run(
            dest,
            "mix",
            "scale",
            "sig",
            "float",
            "float",
            False,
            [],
            doc="TOML override summary.",
        )
        # and a DIFFERENT @brief in the header
        _inject_rich_doxygen(dest)
        apply_run(dest)
        pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
            encoding="utf-8"
        )
        scale_doc = pyi.split("def scale")[1].split('"""')[1]
        assert "TOML override summary." in scale_doc
        # header brief is suppressed by the override
        assert (
            "Scale the input sample by the configured gain." not in scale_doc
        )
        # header @param prose still flows into Parameters
        assert "Sample to scale." in scale_doc

    def test_doc_round_trips_through_toml(self, tmp_path):
        from just_makeit._config import load, methods

        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        module_run(dest, "sig")
        object_run(
            dest,
            "mix",
            module="sig",
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        method_run(
            dest,
            "mix",
            "scale",
            "sig",
            "float",
            "float",
            False,
            [],
            doc="A persisted summary.",
        )
        cfg = load(dest)
        m = next(m for m in methods(cfg, "mix") if m["name"] == "scale")
        assert m.get("doc") == "A persisted summary."
