"""gh-651: one numpy renderer, so the two .pyi generators cannot disagree.

A standalone object's methods are stubbed by ``_context/_methods``; a module
object's by ``_stubs``. Both read the same ``DoxyBlock`` from the same sacred
header, and both are supposed to emit the same numpy docstring — but they were
two hand-written implementations, and they disagreed on three things at once:

- the extended description was dropped entirely on the standalone path;
- a ``Parameters`` entry was a bare ``x`` with no ``: type``, which numpydoc
  does not read as a parameter at all;
- the blank line between sections was eight spaces of trailing whitespace.

The invariant asserted here is the one that matters and the one no unit test
can express: *the same manifest and the same header produce the same
docstring, whether or not the object lives in a module.*
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_RICH = """\
  /**
   * @brief Scale the input sample by the configured gain.
   *
   * The extended description explains how it works and
   * continues onto a second source line.
   *
   * @param x      Sample to scale.
   * @return The scaled sample.
   * @code
   * >>> obj.scale(1.0)
   * 1.0
   * @endcode
   */
"""


def _annotate(dest: Path, comp: str, c_func: str, block: str) -> None:
    """Replace the Doxygen immediately above *c_func* in the sacred header."""
    header = dest / "native" / "inc" / comp / f"{comp}_core.h"
    text = header.read_text(encoding="utf-8")
    decl_re = re.compile(
        r"(?:^[ \t]*/\*\*(?:(?!\*/)[\s\S])*?\*/[ \t]*\r?\n)?"
        r"(^(?![ \t]*[*/])[^\n=]*\b" + c_func + r"\s*\([^;]*\);)",
        re.MULTILINE,
    )
    patched = decl_re.sub(block + r"\1", text, count=1)
    assert patched != text, f"could not locate {c_func} declaration"
    header.write_text(patched, encoding="utf-8")


def _scaffold(dest: Path, *, module: str | None) -> Path:
    new_run("dsp", dest)
    if module:
        module_run(dest, module)
    object_run(
        dest,
        "mix",
        module,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    method_run(dest, "mix", "scale", module, "float", "float", False, [])
    _annotate(dest, "mix", "mix_scale", _RICH)
    apply_run(dest)
    return dest


def _scale_docstring(dest: Path) -> str:
    """Return the verbatim ``def scale`` docstring from the generated stub."""
    for pyi in (dest / "src" / "dsp").rglob("*.pyi"):
        text = pyi.read_text(encoding="utf-8")
        if "def scale" in text:
            start = text.index("def scale")
            open_q = text.index('"""', start)
            close_q = text.index('"""', open_q + 3)
            return text[start : close_q + 3]
    raise AssertionError("no .pyi declares scale()")


class TestStandaloneMatchesModule:
    def test_same_header_same_docstring(self, tmp_path):
        standalone = _scale_docstring(
            _scaffold(tmp_path / "solo" / "p", module=None)
        )
        modular = _scale_docstring(
            _scaffold(tmp_path / "mod" / "p", module="sig")
        )
        assert standalone == modular

    def test_standalone_carries_what_it_used_to_drop(self, tmp_path):
        doc = _scale_docstring(_scaffold(tmp_path / "solo" / "p", module=None))

        # The extended description, previously dropped on this path entirely.
        assert "The extended description explains how it works" in doc

        # A typed Parameters entry. `x` alone is not a numpydoc parameter.
        assert "        x : float\n" in doc
        assert "\n        x\n" not in doc

        # No trailing-whitespace separator lines.
        assert not any(ln != ln.rstrip() for ln in doc.splitlines()), (
            "generated docstring carries trailing whitespace"
        )

        # And the parts that already worked still do.
        assert doc.startswith("def scale(self, x: float) -> float:")
        assert "Sample to scale." in doc
        assert "The scaled sample." in doc
        assert ">>> obj.scale(1.0)" in doc
