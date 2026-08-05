"""gh-744: no generated ``.pyi`` line exceeds 79 columns.

doppler measured **1471** overlong lines across 34 stubs, with the worst at
1396 columns. The stubs are jm-owned and drift-gated, so a downstream project
cannot fix this locally -- hand-wrapping a stub, or running a formatter over
it, is drift. jm is the only place it can be fixed, which is what makes the
width a *generator* contract rather than a downstream lint preference.

Four causes, four renderers, and they are tested here together because the
acceptance criterion is a property of the whole file, not of any one of them:

1. ``_docstring._numpy_sections`` spliced the summary in unwrapped.
2. Its wrap widths (72/68/69) were chosen without the caller's 8-space indent
   in the budget, so "wrapped" prose landed on column 80.
3. ``_stubs._build_class_docstring`` -- a separate renderer -- wrapped nothing
   at all. The 1222-column parameter description came from here.
4. ``_context/_methods`` emitted a property docstring on one line always.

Plus the signatures, which are reflowed after the fact by ``_pyfmt`` rather
than at 63 separate emission sites; see that module's docstring for why.

The last classes below are the ones that matter most in six months: they pin
that *every* stub producer reflows. Applying a transform on the write path and
not on the compare path is what gh-635 records for the C side, and it leaves
every project permanently stale.

gh-747 extended this file after two of the five producers (`_handle`,
`_capsule`) turned out never to have been wired up, and the naming gate at the
bottom now *enumerates* producers rather than listing them — because naming
them is precisely how the two were missed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._stubs import _ctor_demo_lines  # noqa: E402
from just_makeit._pyfmt import (  # noqa: E402
    _split_top_level,
    reflow_line,
    reflow_pyi,
)

TARGET = 79

# Deliberately awful input: each of these is longer than any single line may
# be, so a renderer that does not wrap cannot accidentally pass.
_LONG_BRIEF = (
    "Demodulate a real f32 block and return the recovered M-PSK symbols, one "
    "cf32 per recovered symbol period, with the carrier and timing loops both "
    "closed and the code-lock detector running continuously alongside them."
)
_LONG_PARAM = (
    "Terminal outputs per symbol: even, 2..8. The Gardner detector takes "
    "every m_out-th output as the on-time strobe and the one m_out/2 before "
    "it as the early sample, so the value sets both the loop rate and the "
    "interpolator's phase granularity."
)
_LONG_PROP = (
    "Where fs and fc came from -- file when the capture declared them, "
    "supplied when you passed them to the constructor, or none when neither. "
    "This is the property the view exists to make honest."
)


def _document_create(root, comp, brief, param, pname):
    """Put a long ``@brief`` and ``@param`` on the sacred ``create()``.

    The class docstring derives from this block, so it is the input that
    exercises ``_build_class_docstring`` rather than ``_numpy_sections``.
    """
    h = root / "native" / "inc" / comp / f"{comp}_core.h"
    text = h.read_text(encoding="utf-8")
    marker = f"{comp}_state_t *{comp}_create("
    idx = text.index(marker)
    start = text.rfind("/**", 0, idx)
    end = text.index("*/", start) + 2
    block = f"/**\n * @brief {brief}\n *\n * @param {pname} {param}\n */"
    h.write_text(text[:start] + block + text[end:], encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    """A standalone object whose header is documented past the column limit."""
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "receiver",
        None,
        state_vars=[("gain", "double", "1.0")],
        init_params=[("m_out", "int", "8")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    property_run(
        root, "receiver", "source", None, "int", False, doc=_LONG_PROP
    )
    _document_create(root, "receiver", _LONG_BRIEF, _LONG_PARAM, "m_out")
    apply_run(root)
    return root


@pytest.fixture
def module_project(tmp_path):
    """The same documentation, through the module aggregator instead."""
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "radio")
    object_run(
        root,
        "receiver",
        "radio",
        state_vars=[("gain", "double", "1.0")],
        init_params=[("m_out", "int", "8")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    property_run(
        root, "receiver", "source", "radio", "int", False, doc=_LONG_PROP
    )
    _document_create(root, "receiver", _LONG_BRIEF, _LONG_PARAM, "m_out")
    apply_run(root)
    return root


def _stubs(root):
    found = sorted(root.rglob("*.pyi"))
    assert found, "no .pyi generated -- the fixture is not exercising anything"
    return found


def _overlong(path):
    return [
        (n, len(ln), ln)
        for n, ln in enumerate(path.read_text(encoding="utf-8").split("\n"), 1)
        if len(ln) > TARGET
    ]


class TestGeneratedStubsFitTheTarget:
    """The acceptance criterion from the issue, on both stub producers."""

    def test_standalone_stub_has_no_overlong_line(self, project):
        for path in _stubs(project):
            bad = _overlong(path)
            assert not bad, f"{path.name}: {bad[:3]}"

    def test_module_stub_has_no_overlong_line(self, module_project):
        for path in _stubs(module_project):
            bad = _overlong(path)
            assert not bad, f"{path.name}: {bad[:3]}"

    def test_the_long_prose_actually_reached_the_stub(self, project):
        """Guard the guard: wrapping nothing would also pass the test above."""
        pyi = (project / "src" / "demo" / "receiver.pyi").read_text()
        flat = " ".join(pyi.split())
        assert "Demodulate a real f32 block" in flat
        assert "the interpolator's phase granularity" in flat
        assert "the property the view exists to make honest" in flat

    def test_the_stub_is_still_valid_python(self, project):
        import ast

        for path in _stubs(project):
            ast.parse(path.read_text(encoding="utf-8"))


class TestBothProducersReflow:
    """Reflow on the write path only would make every project stale."""

    def test_apply_leaves_no_drift_standalone(self, project):
        assert _status.run(project, check=True) == 0

    def test_apply_leaves_no_drift_module(self, module_project):
        assert _status.run(module_project, check=True) == 0

    def test_component_pyi_has_exactly_one_door(self):
        """No module may render ``COMPONENT_PYI`` without the reflow.

        A seventh call site added later would silently reintroduce the
        write/compare asymmetry, and the failure mode -- permanent phantom
        drift in a downstream project -- points nowhere near this change.
        """
        src = Path(__file__).parent.parent / "src" / "just_makeit"
        offenders = []
        for path in sorted(src.rglob("*.py")):
            if path.name in ("_render.py", "_pyfmt.py"):
                continue
            for n, line in enumerate(path.read_text().split("\n"), 1):
                if "COMPONENT_PYI" in line and "render" in line:
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        assert not offenders, (
            "render COMPONENT_PYI via _render.render_component_pyi:\n"
            + "\n".join(offenders)
        )


class TestReflow:
    """``_pyfmt`` on its own, including what it must refuse to touch."""

    def test_splits_at_top_level_commas_only(self):
        assert _split_top_level("a: int, b: str") == ["a: int", "b: str"]
        assert _split_top_level("x: dict[str, int]") == []

    def test_a_comma_inside_a_string_is_not_a_separator(self):
        assert _split_top_level('sep: str = ","') == []

    def test_short_lines_are_untouched(self):
        line = "    def step(self, x: float) -> float: ..."
        assert reflow_line(line) == [line]

    def test_an_unsplittable_signature_is_left_alone(self):
        """One parameter and a long annotation: no comma to break at.

        Left long on purpose -- breaking inside the annotation would be a
        guess about what reads well. The issue names this case explicitly.
        """
        line = '    def kind(self) -> Literal["' + "a" * 90 + '"]: ...'
        assert reflow_line(line) == [line]

    def test_reflow_is_idempotent(self):
        src = (
            "class A:\n"
            "    def f(self, alpha: int = 1, beta: str = 'x',"
            " gamma: float = 2.0, delta: bytes = b'') -> None: ...\n"
        )
        once = reflow_pyi(src)
        assert reflow_pyi(once) == once
        assert max(len(ln) for ln in once.split("\n")) <= TARGET

    def test_a_doctest_inside_a_docstring_is_never_reflowed(self):
        """Breaking a ``>>>`` line would stop the example from running."""
        call = ">>> obj = Thing(" + ", ".join(f"arg{i}=1" for i in range(12))
        src = f'class A:\n    """Doc.\n\n    {call})\n    """\n'
        assert len(max(src.split("\n"), key=len)) > TARGET  # precondition
        assert reflow_pyi(src) == src

    def test_a_spliced_summary_line_is_wrapped(self):
        """``_swap_pyi_summary`` puts the header brief on the opening line.

        It builds the ``step()``/``steps()`` blocks per I/O shape and then
        substitutes only the summary, so the wrap `_docstring` applies at
        render time never reaches it. 19 of doppler's stubs hit this.
        """
        line = '        """' + "word " * 40 + "end."
        out = reflow_pyi(line + '\n        """\n')
        assert max(len(ln) for ln in out.split("\n")) <= TARGET
        assert out.split("\n")[0].startswith('        """word')

    def test_prose_inside_a_multiline_docstring_is_left_alone(self):
        """Wrapping the prose is ``_docstring``'s job, done at its source.

        Reflowing it again here would fight that renderer, and would reflow
        an author's hand-written ``@code`` block along with it.
        """
        src = (
            'def f() -> None:\n    """Summary.\n\n    '
            + "word " * 30
            + '\n    """\n'
        )
        assert reflow_pyi(src) == src

    def test_a_complete_one_line_docstring_is_reflowed(self):
        """The literal one-liners jm hard-codes are in scope, though.

        They carry no structure to preserve, and pinning each literal to a
        remembered length is what let an 82-column one ship.
        """
        src = '    """' + "word " * 20 + '."""\n'
        out = reflow_pyi(src)
        assert out != src
        assert max(len(ln) for ln in out.split("\n")) <= TARGET
        assert reflow_pyi(out) == out


class TestSynthesisedConstructorDemo:
    """jm's own ``>>> obj = Component(...)`` line owns its width.

    An author's ``@code`` block is preserved byte-for-byte -- trailing
    comment alignment and all -- so it is deliberately out of scope. This
    line is not an author's; it is jm's, and doppler's ``MpskReceiver``
    turned it into 257 columns.
    """

    def test_a_long_constructor_demo_wraps(self):
        args = ", ".join(f"param_{i}={i}.0" for i in range(12))
        lines = _ctor_demo_lines("Receiver", args)
        assert len(lines) > 1
        assert max(len(ln) for ln in lines) <= TARGET

    def test_the_wrapped_demo_is_still_one_doctest_statement(self):
        args = ", ".join(f"param_{i}={i}.0" for i in range(12))
        lines = _ctor_demo_lines("Receiver", args)
        assert lines[0].lstrip().startswith(">>> ")
        assert all(ln.lstrip().startswith("... ") for ln in lines[1:-1])
        assert lines[-1].strip() == "... )"
        # It must still be the call it was, once the prompts come off.
        src = "\n".join(ln.strip()[4:] for ln in lines)
        import ast

        ast.parse(src)

    def test_a_short_demo_keeps_its_single_line(self):
        """No churn for the overwhelming majority that already fit."""
        assert _ctor_demo_lines("Fir", "gain=1.0") == [
            "    >>> obj = Fir(gain=1.0)"
        ]

    def test_an_unsplittable_single_argument_is_left_readable(self):
        long_arg = "mode=" + '"' + "x" * 90 + '"'
        assert _ctor_demo_lines("Fir", long_arg) == [
            f"    >>> obj = Fir({long_arg})"
        ]


# ── gh-747: all five producers, found rather than named ──────────────────────


def _handle_cfg_with_long_prose():
    """A handle module whose every docstring surface is deliberately overlong.

    A long ``@brief`` (the class summary), a long ``@param`` description, and
    a signature past 79 columns — the three surfaces `_handle` emitted raw
    before gh-747, and the shape of doppler's `sample_clock` / `wfm_sink`.
    """
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [{"name": "ftype", "values": ["raw", "csv", "sigmf"]}],
        "module": {
            "tracker": {
                "kind": "handle",
                "backing": "tracker",
                "type_name": "SampleClock",
                "create_fn": "tracker_open",
                "close_fn": "tracker_close",
                "create_args": [
                    {"name": "observed_timestamp_ns", "type": "size_t"},
                    {"name": "n_at_observation", "type": "size_t"},
                    {"name": "tolerance_ns", "type": "size_t"},
                    {
                        "name": "file_type",
                        "type": "int",
                        "enum": "ftype",
                        "default": "raw",
                    },
                ],
                "methods": [
                    {
                        "name": "track",
                        "fn": "tracker_track",
                        "returns": "size_t",
                        "args": [
                            {
                                "name": "observed_timestamp_ns",
                                "type": "size_t",
                            },
                            {"name": "n_at_observation", "type": "size_t"},
                            {"name": "tolerance_ns", "type": "size_t"},
                        ],
                    }
                ],
            }
        },
    }


def _capsule_cfg_with_long_prose():
    """A capsule module whose create and method signatures both exceed 79.

    The parameter names are long on purpose. doppler has no capsule module
    that overflows, so a fixture built from a realistic one would assert
    nothing — the test would pass with the reflow removed, which is exactly
    the false green that let the capsule half sit unnoticed.
    """
    return {
        "project": {"name": "proj", "version": "0.1.0"},
        "module": {
            "ddc_fn": {
                "kind": "capsule",
                "backing": "ddcr",
                "capsule_name": "proj.ddc.ddcr_state",
                "header": "ddc/ddc_core.h",
                "init_params": [
                    {"name": "normalised_centre_frequency", "type": "double"},
                    {"name": "decimation_rate_in_samples", "type": "double"},
                    {"name": "loop_bandwidth_normalised", "type": "double"},
                ],
                "methods": [
                    {
                        "name": "execute_with_explicit_output_buffer",
                        "arg_type": "float[]",
                        "return_type": "float _Complex[]",
                        "caller_out": True,
                    },
                ],
            }
        },
    }


class TestHandleAndCapsuleProducers:
    """gh-747: the two producers gh-744 missed.

    doppler carried three handle modules and no capsule ones, so the handle
    half is the measured case and the capsule half is prevention.
    """

    def test_handle_stub_has_no_overlong_line(self):
        from just_makeit import _handle

        pyi = _handle.render_pyi(_handle_cfg_with_long_prose(), "tracker")
        over = [ln for ln in pyi.split("\n") if len(ln) > TARGET]
        assert not over, "\n".join(f"{len(ln)}: {ln}" for ln in over)

    def test_the_long_handle_signature_actually_reached_the_stub(self):
        """Guards the fixture: a stub that never got long input proves nothing."""
        from just_makeit import _handle
        from just_makeit._pyfmt import flatten_signatures

        pyi = flatten_signatures(
            _handle.render_pyi(_handle_cfg_with_long_prose(), "tracker")
        )
        flat = [ln for ln in pyi.split("\n") if "def track(" in ln]
        assert flat and len(flat[0]) > TARGET, (
            "fixture no longer produces an overlong signature: " + str(flat)
        )

    def test_capsule_stub_has_no_overlong_line(self):
        from just_makeit import _capsule

        pyi = _capsule.render_pyi(_capsule_cfg_with_long_prose(), "ddc_fn")
        over = [ln for ln in pyi.split("\n") if len(ln) > TARGET]
        assert not over, "\n".join(f"{len(ln)}: {ln}" for ln in over)

    def test_the_long_capsule_signature_actually_reached_the_stub(self):
        """Guards the fixture, as above — it passed vacuously when written."""
        from just_makeit import _capsule
        from just_makeit._pyfmt import flatten_signatures

        pyi = flatten_signatures(
            _capsule.render_pyi(_capsule_cfg_with_long_prose(), "ddc_fn")
        )
        flat = [
            ln
            for ln in pyi.split("\n")
            if "def ddcr_execute_with_explicit_output_buffer(" in ln
        ]
        assert flat and len(flat[0]) > TARGET, (
            "fixture no longer produces an overlong signature: " + str(flat)
        )

    def test_handle_stub_is_still_valid_python(self):
        import ast

        from just_makeit import _handle

        pyi = _handle.render_pyi(_handle_cfg_with_long_prose(), "tracker")
        ast.parse(pyi)


class TestEveryProducerReflows:
    """The registration-free gate the issue asked for.

    gh-744 named its producers and found five; gh-747 then found that two of
    the five were missed and that the class-docstring *builders* inside them
    had never been counted at all. A test that enumerates cannot make that
    mistake: a sixth producer is covered on the day it is written, without
    anyone remembering to add it here.
    """

    @staticmethod
    def _producers():
        """Every function returning a complete ``.pyi`` document.

        Matched by name (``render_pyi`` / ``render_*_pyi`` / ``make_*_pyi``)
        **and** a ``-> str`` return annotation. The annotation is what
        separates a document producer from a fragment builder:
        ``_codec.render_method_pyi`` returns ``list[str]`` spliced into a
        component stub that is reflowed at its own door, so requiring it to
        reflow its own two lines would be wrong.

        Honest limitation: a producer named outside that convention is not
        found. The convention is what the package follows today, and a gate
        over it beats the hand-written list it replaces.
        """
        import ast
        import re

        pat = re.compile(r"^(render_pyi|render_\w+_pyi|make_\w+_pyi)$")
        src = Path(__file__).parent.parent / "src" / "just_makeit"
        found = []
        for path in sorted(src.rglob("*.py")):
            # templates/ holds `<<token>>` placeholder files, not Python.
            if "templates" in path.parts:
                continue
            text = path.read_text()
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if not pat.match(node.name):
                    continue
                ret = ast.unparse(node.returns) if node.returns else ""
                if ret != "str":
                    continue
                found.append(
                    (
                        f"{path.name}:{node.lineno} {node.name}",
                        ast.get_source_segment(text, node) or "",
                    )
                )
        return found

    def test_the_enumeration_finds_the_known_producers(self):
        """A pattern that matches nothing would make the gate below vacuous."""
        names = [n for n, _ in self._producers()]
        assert len(names) >= 5, names
        for expected in (
            "_render.py",
            "_stubs.py",
            "_composer.py",
            "_handle.py",
            "_capsule.py",
        ):
            assert any(n.startswith(expected) for n in names), (
                f"{expected} producer not found by the enumeration: {names}"
            )

    def test_every_producer_routes_through_the_reflow(self):
        offenders = [
            name
            for name, body in self._producers()
            if "reflow_pyi" not in body
        ]
        assert not offenders, (
            "these .pyi producers do not reflow their output; a long "
            "signature or docstring will ship overlong:\n  "
            + "\n  ".join(offenders)
        )
