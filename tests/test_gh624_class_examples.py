"""gh-624: a `@code` on create() becomes the class's Examples section.

The header is the documentation source for methods -- a `@brief` becomes the
docstring, a `@code` becomes a runnable doctest -- but a class's `Examples` was
*always* jm's synthesised "Create with defaults" demo. A rich `@code` on
`<obj>_create` did not reach it, so the one example a reader sees first could
only ever show construction, never what the type is for.

Two behaviours are pinned here.

**An authored example wins.** jm's synthesised demo is a fallback for a header
that says nothing, not something an author should have to override.

**It is checked before the unseedable-ctor suppression.** jm refuses to emit a
generated construction example when it cannot seed every argument (an array
arg, a required init-param with no default, a `path` -- gh-273/gh-515), because
the generated `.pyi` doctests are executed and a fabricated call would raise.
That left exactly the objects whose authors most need to show a real example
with no Examples section at all. An authored block has none of that problem:
the author wrote a call that works.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import parse_doxygen_block  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._stubs import _build_class_docstring  # noqa: E402

_CODE = (
    "@brief Adaptive widget with a tuned response.\n"
    "\n"
    "@code\n"
    ">>> from demo import Widget\n"
    ">>> w = Widget(gain=2.0)\n"
    ">>> w.step(1.0)\n"
    "2.0\n"
    "@endcode"
)


def _doc(create_blk=None, *, py_create_args="gain=1.0", init_params=()):
    return "\n".join(
        _build_class_docstring(
            "Widget",
            [("gain", "float", "1.0f")],
            False,
            list(init_params),
            "from demo import Widget",
            py_create_args,
            brief="Adaptive widget.",
            create_blk=create_blk,
        )
    )


class TestAuthoredExampleWins:
    def test_code_block_becomes_the_examples_section(self):
        out = _doc(parse_doxygen_block(_CODE))
        assert ">>> w = Widget(gain=2.0)" in out
        assert ">>> w.step(1.0)" in out

    def test_synthesised_demo_is_replaced_not_appended(self):
        # Two Examples sections, or the canned demo trailing the author's,
        # would be worse than either alone.
        out = _doc(parse_doxygen_block(_CODE))
        assert out.count("Examples") == 1
        assert "Create with defaults:" not in out
        assert "Reset restores defaults:" not in out

    def test_no_code_block_keeps_the_synthesised_demo(self):
        out = _doc(parse_doxygen_block("@brief Adaptive widget."))
        assert "Create with defaults:" in out

    def test_absent_block_keeps_the_synthesised_demo(self):
        assert "Create with defaults:" in _doc(None)


class TestUnseedableConstructor:
    """The case that previously had no Examples section at all."""

    _UNSEEDABLE = [
        {"name": "path", "type": "path", "default": "", "required": True}
    ]

    def test_authored_example_survives_the_suppression(self):
        out = _doc(
            parse_doxygen_block(_CODE),
            py_create_args="...",
            init_params=self._UNSEEDABLE,
        )
        assert "Examples" in out, (
            "an authored example is exactly what an unseedable constructor "
            "needs, and it is the author's own working call"
        )
        assert ">>> w = Widget(gain=2.0)" in out

    def test_still_suppressed_without_an_authored_example(self):
        # jm must not fabricate a call it cannot seed -- the generated .pyi
        # doctests are executed.
        out = _doc(
            parse_doxygen_block("@brief Adaptive widget."),
            py_create_args="...",
            init_params=self._UNSEEDABLE,
        )
        assert "Examples" not in out


class TestEndToEnd:
    def test_header_code_reaches_the_generated_stub(self, tmp_path):
        root = tmp_path / "demo"
        new_run("demo", root)
        object_run(
            root,
            "widget",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        h = root / "native" / "inc" / "widget" / "widget_core.h"
        text = h.read_text(encoding="utf-8")
        assert " * @brief Create a widget instance." in text
        h.write_text(
            text.replace(
                " * @brief Create a widget instance.",
                " * @brief Adaptive widget with a tuned response.\n"
                " *\n"
                " * @code\n"
                " * >>> from demo import Widget\n"
                " * >>> w = Widget(gain=2.0)\n"
                " * @endcode",
            ),
            encoding="utf-8",
        )
        apply_run(root)
        pyi = (root / "src" / "demo" / "widget.pyi").read_text(
            encoding="utf-8"
        )
        assert ">>> w = Widget(gain=2.0)" in pyi
        assert "Create with defaults:" not in pyi.split("def ")[0]
