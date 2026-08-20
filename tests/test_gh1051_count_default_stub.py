"""gh-1051: the `.pyi` default for `count` is the one the binding uses.

A `variable_output` method with `arg_type = "void"` binds its capacity as a
`count` keyword, seeded from the manifest's `count_default` — a C expression
evaluated before `PyArg_ParseTupleAndKeywords`. So a method declaring one has
a zero-arg behaviour that is emphatically *not* `count=1`.

The two `.pyi` generators disagreed about that, for the same manifest and the
same method:

===================================  ==================
`_context/_methods` (standalone)     ``count: int = ...``
`_stubs` (module-aggregated)         ``count: int = 1``
===================================  ==================

doppler's `ReedSolomon.generator` declares
``count_default = "state->rs.code.nroots + 1"`` and is a module object, so its
stub advertised `1` — a length the kernel refuses. A type checker, an IDE
tooltip and `help()` all repeat it.

gh-657 fixed the stub *omitting* `count` and did not carry the value through,
taking it from "missing" to "present and wrong" — and the comment directly
above the hard-coded `1` described the `count_default` behaviour it was not
implementing.

**This is the second time these two generators were caught disagreeing about
jm's own binding arguments** (gh-1042 was the first, over whether they are
documented at all). So the answer lives in one place, and
:class:`TestTheTwoGeneratorsAgree` compares them directly rather than
asserting the same literal twice — a test that pins each face separately is
one edit away from pinning two different answers.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._gluedoc import count_stub_default  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _count_line(pyi: Path, name: str) -> str:
    """The rendered `count: int = <default>` for *name*."""
    text = pyi.read_text(encoding="utf-8")
    m = re.search(rf"    def {name}\((.*?)\) ->", text, re.S)
    assert m, f"no {name}() in {pyi.name}"
    line = re.search(r"count: int = (\S+?),", m.group(1))
    assert line, f"{name}() has no count arg:\n{m.group(1)}"
    return line.group(1)


def _project(tmp_path: Path, count_default: str) -> Path:
    """One manifest, the same method on a standalone AND a module object."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "cod")
    for obj, mod in (("solo", None), ("rs", "cod")):
        _quiet(
            object_run,
            root,
            obj,
            mod,
            state_vars=[("nroots", "int", "0")],
            arg_type="void",
            return_type="uint8_t",
        )
        kw = {"count_default": count_default} if count_default else {}
        _quiet(
            method_run,
            root,
            obj,
            "generator",
            mod,
            "void",
            "uint8_t",
            True,
            [],
            **kw,
        )
    return root


class TestTheTwoGeneratorsAgree:
    """The property that matters: one manifest, one answer.

    Compared against each other, not against a literal. Pinning each face to
    its own expected string is exactly how they came to hold two different
    answers in the first place.
    """

    @pytest.mark.parametrize(
        "count_default",
        ["", "4", "state->nroots + 1", "NROOTS_MAX"],
        ids=["absent", "literal", "expression", "macro"],
    )
    def test_standalone_and_module_render_the_same_default(
        self, tmp_path, count_default
    ):
        root = _project(tmp_path, count_default)
        standalone = _count_line(root / "src/demo/solo.pyi", "generator")
        module = _count_line(root / "src/demo/cod/cod.pyi", "generator")
        assert standalone == module, (
            f"count_default={count_default!r}: standalone renders "
            f"{standalone!r}, module renders {module!r}"
        )


class TestTheRenderedValue:
    """...and that the shared answer is the right one."""

    def test_an_expression_is_not_claimed_as_a_literal(self, tmp_path):
        """The reported bug: `1` is a length the kernel refuses.

        `...` is the stub's way of saying "there is a default and it is not
        written here", which is honest where a literal would be a lie.
        """
        root = _project(tmp_path, "state->nroots + 1")
        assert _count_line(root / "src/demo/cod/cod.pyi", "generator") == "..."

    def test_an_integer_literal_renders_as_itself(self, tmp_path):
        """Truthful, and better than the ellipsis both faces used to show."""
        root = _project(tmp_path, "4")
        assert _count_line(root / "src/demo/cod/cod.pyi", "generator") == "4"

    def test_no_declared_default_still_renders_one(self, tmp_path):
        """`1` is genuinely what the binding uses when nothing is declared.

        The guard against 'fixing' this by rendering `...` everywhere, which
        would lose a default that is real.
        """
        root = _project(tmp_path, "")
        assert _count_line(root / "src/demo/solo.pyi", "generator") == "1"

    def test_the_binding_really_seeds_from_the_expression(self, tmp_path):
        """The premise. Without this the stub could be 'wrong' about nothing.

        gh-1042's lesson: an assertion about a doc face is worth only as much
        as the check that the other face says something different.
        """
        root = _project(tmp_path, "state->nroots + 1")
        ext = (root / "native/src/cod/cod_ext_rs.c").read_text(
            encoding="utf-8"
        )
        assert "state->nroots + 1" in ext, ext[:400]


class TestTheHelper:
    """Unit-level, so a break localises off the generators."""

    @pytest.mark.parametrize(
        "expr,want",
        [
            ("", "1"),
            ("4", "4"),
            ("  16  ", "16"),
            ("0", "0"),
            ("state->rs.code.nroots + 1", "..."),
            ("NROOTS", "..."),
            ("1 + 1", "..."),
            ("-1", "..."),
        ],
    )
    def test_only_a_bare_integer_survives_as_a_literal(self, expr, want):
        assert count_stub_default(expr) == want
