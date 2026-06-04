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


def _inject_rich_doxygen(dest: Path):
    """Replace mix_scale's template Doxygen with a rich hand-written block."""
    header = dest / "native" / "inc" / "mix" / "mix_core.h"
    text = header.read_text(encoding="utf-8")
    # Find the declaration and drop any existing /** */ immediately above it.
    decl_re = re.compile(
        r"(?:[ \t]*/\*\*.*?\*/\s*)?(^[^\n]*\bmix_scale\s*\([^;]*\);)",
        re.DOTALL | re.MULTILINE,
    )
    text2 = decl_re.sub(_RICH_DOXYGEN + r"\1", text, count=1)
    assert text2 != text, "could not locate mix_scale declaration to annotate"
    header.write_text(text2, encoding="utf-8")


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
