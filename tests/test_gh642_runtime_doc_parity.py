"""gh-642: the runtime ``__doc__`` must carry the block the ``.pyi`` carries.

jm rendered the full numpy block (summary, extended description, Parameters,
Returns, Examples) into the type stub, but the **runtime** literals -- the
``PyMethodDef`` doc, ``tp_doc`` -- got the ``@brief`` alone. So ``help(obj)``
in a REPL, which is where someone actually asks "how do I use this?", showed
drastically less than a stub file they never open. doppler measured the gap at
789 stub-incomplete against 988 runtime-incomplete of 1384 public surfaces.

The fix was not "render more at runtime" but "render *the same thing*": both
faces now go through one section builder
(:func:`_docstring._numpy_sections`), so the runtime text **is** the stub text
with the indent and the ``\"\"\"`` delimiters removed. That equality is the
invariant this file gates, because it is the only formulation that cannot rot
-- a future section added to one face appears on the other for free, and any
attempt to special-case one of them fails here.

Two layers, deliberately:

1. :class:`TestRendererInvariant` pins the equality at the renderer, over a
   corpus of block shapes. Cheap, and it localises a break to the renderer.
2. :class:`TestGeneratedProject` proves it survives the generators -- the
   thing that was actually broken. A renderer nobody calls is exactly the
   failure mode the four brief-only shapes represented.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    DoxyBlock,
    render_numpy_doc,
    render_runtime_doc,
)
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# ── corpus ──────────────────────────────────────────────────────────────────
# One entry per structural shape a header block can take. `None` is the
# undocumented case, which must still agree between the faces.
BLOCKS: dict[str, DoxyBlock | None] = {
    "brief_only": DoxyBlock(brief="Filter a block."),
    "brief_and_body": DoxyBlock(
        brief="Filter a block.",
        body=["State carries across calls.", "", "Cost is O(n * taps)."],
    ),
    "params_and_return": DoxyBlock(
        brief="Filter a block.",
        params=[("x", "Input samples."), ("gain", "Linear scale.")],
        returns="Filtered output.",
    ),
    "with_examples": DoxyBlock(
        brief="Filter a block.",
        params=[("x", "Input samples.")],
        returns="Filtered output.",
        examples=[">>> f.run(x)", "array([0.], dtype=float32)"],
    ),
    "long_prose_wraps": DoxyBlock(
        brief="Filter a block.",
        body=[" ".join(["word"] * 60)],
        params=[("x", " ".join(["described"] * 30))],
        returns=" ".join(["returned"] * 30),
    ),
    "undocumented": None,
}

PY_PARAMS = [("x", "NDArray[np.float32]"), ("gain", "float")]


def _stub_sections(
    block: DoxyBlock | None, ret_ann: str, indent: int
) -> list[str]:
    """The ``.pyi`` docstring reduced to bare section lines.

    Removes exactly what the runtime face is allowed to differ by: the
    indent and the two ``\"\"\"`` delimiters. Anything else surviving here is
    a real divergence.
    """
    lines = render_numpy_doc(
        block,
        "run",
        PY_PARAMS,
        ret_ann,
        indent=indent,
        skeleton_fallback=True,
    )
    pad = " " * indent
    out = [lines[0][indent:].removeprefix('"""')]
    out += [ln[indent:] if ln.startswith(pad) else ln for ln in lines[1:-1]]
    assert lines[-1].strip() == '"""', "stub block lost its terminator"
    while out and not out[-1].strip():
        out.pop()
    return out


class TestRendererInvariant:
    """The runtime block is the stub block, dedented and undelimited."""

    @pytest.mark.parametrize("shape", sorted(BLOCKS), ids=sorted(BLOCKS))
    @pytest.mark.parametrize("ret_ann", ["NDArray[np.float32]", "None"])
    @pytest.mark.parametrize("indent", [4, 8])
    def test_faces_are_the_same_text(self, shape, ret_ann, indent):
        block = BLOCKS[shape]
        runtime = render_runtime_doc(block, "run", PY_PARAMS, ret_ann)
        assert runtime == _stub_sections(block, ret_ann, indent)

    def test_the_check_can_fail(self):
        """The comparison is not vacuously true on every input.

        Two different blocks must not compare equal, or the test above would
        pass against any renderer at all.
        """
        a = render_runtime_doc(BLOCKS["brief_only"], "run", PY_PARAMS, "None")
        b = _stub_sections(BLOCKS["brief_and_body"], "None", 8)
        assert a != b

    def test_examples_reach_the_runtime_face(self):
        """The section doppler asked us to drop; we render it (see gh-642)."""
        out = render_runtime_doc(
            BLOCKS["with_examples"], "run", PY_PARAMS, "NDArray[np.float32]"
        )
        assert "Examples" in out
        assert ">>> f.run(x)" in out

    def test_undocumented_member_gets_no_examples_section(self):
        out = render_runtime_doc(BLOCKS["brief_only"], "run", PY_PARAMS, "int")
        assert "Examples" not in out


# ── generated-project layer ─────────────────────────────────────────────────

_AUTHORED = """ * @brief Filter one block of samples through the FIR.
 *
 * The kernel walks the delay line once per output sample, so cost is
 * O(len(x) * num_taps).
 *
 * @param in Input samples. Any length, including zero.
 * @param gain Linear scale applied after the convolution.
 * @return Filtered output, same length as @p in.
 *
 * @code
 * >>> out = obj.run(x, 1.0)
 * >>> out.ndim
 * 1
 * @endcode"""

_FREE_FN_DOC = """/**
 * @brief Build a Kaiser window of the requested length.
 *
 * The shape parameter trades main-lobe width against side-lobe level.
 *
 * @param n Number of samples in the window.
 * @param beta Shape parameter. Larger is more tapered.
 * @return The window, `n` samples long.
 */
"""

_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _runtime_doc(ext_c: str, method: str) -> list[str]:
    """The ``PyMethodDef`` doc literal for *method*, unescaped to lines."""
    start = ext_c.index(f'{{"{method}",')
    out: list[str] = []
    for raw in ext_c[start:].splitlines()[1:]:
        m = _C_DOC_LINE.match(raw)
        if not m:
            break
        out.append(m.group(1))
    assert out, f"no doc literal found for {method}()"
    text = "".join(out).encode().decode("unicode_escape")
    return text.split("\n")


def _stub_doc(pyi: str, method: str) -> list[str]:
    """The ``.pyi`` docstring for *method*, dedented and undelimited."""
    m = re.search(
        rf'    def {method}\([^\n]*\n        """(.*?)\n        """',
        pyi,
        re.S,
    )
    assert m, f"no stub docstring found for {method}()"
    body = m.group(1).split("\n")
    return [body[0]] + [
        ln[8:] if ln.startswith("        ") else ln for ln in body[1:]
    ]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A scaffold whose run() carries a fully authored Doxygen block."""
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "fir",
        None,
        state_vars=[("num_taps", "int", "4")],
        arg_type="void",
        return_type="float",
    )
    method_run(
        root,
        "fir",
        "run",
        None,
        "float[]",
        "float",
        True,
        [],
        params=[("gain", "double")],
    )
    header = root / "native" / "inc" / "fir" / "fir_core.h"
    text = header.read_text(encoding="utf-8")
    assert " * @brief run." in text, "the scaffold no longer seeds @brief run."
    header.write_text(
        text.replace(" * @brief run.", _AUTHORED, 1), encoding="utf-8"
    )
    apply_run(root)
    return root


class TestGeneratedProject:
    """What the generators actually write into a project."""

    def test_runtime_block_matches_the_stub(self, project):
        ext_c = (project / "native/src/fir/fir_ext.c").read_text()
        pyi = (project / "src/demo/fir.pyi").read_text()
        runtime = _runtime_doc(ext_c, "run")
        stub = _stub_doc(pyi, "run")
        # The runtime literal leads with a signature line the stub does not
        # need (the stub has a real `def`), and trailing blanks differ.
        assert runtime[0].startswith("run("), runtime[0]
        body = [ln for ln in runtime[2:] if ln.strip()]
        assert body == [ln for ln in stub if ln.strip()]

    def test_authored_prose_reaches_the_runtime_face(self, project):
        """The regression itself: @param/@return text in the .so, not just
        the stub."""
        ext_c = (project / "native/src/fir/fir_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "run"))
        assert "Input samples. Any length, including zero." in runtime
        assert "Linear scale applied after the convolution." in runtime
        assert "Filtered output, same length as in." in runtime
        assert "The kernel walks the delay line" in runtime

    def test_signature_line_lists_every_documented_param(self, project):
        """A doc that contradicts itself is worse than a thin one.

        The variable_output shape hard-coded `x` and dropped declared params,
        so rendering Parameters made one docstring advertise `run(x)` above a
        block documenting `gain` -- the gh-657 failure shape.
        """
        ext_c = (project / "native/src/fir/fir_ext.c").read_text()
        assert _runtime_doc(ext_c, "run")[0].startswith("run(x, gain)")

    def test_authored_example_replaces_the_synthesised_demo(self, project):
        """One Examples section, not the author's plus jm's placeholder."""
        ext_c = (project / "native/src/fir/fir_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "run"))
        assert ">>> out = obj.run(x, 1.0)" in runtime
        assert runtime.count("Examples") == 1
        assert ">>> from demo import Fir" not in runtime

    def test_module_free_function_matches_its_stub(self, tmp_path):
        """gh-643: the module-level twin.

        A `[module.X]` free function got a full `.pyi` docstring from the
        module header (gh-384) while its runtime doc was
        `fn["doc"] or "{name}."` -- so `help(kaiser_window)` never saw the C
        `@brief`, let alone params or returns. Same invariant, same renderer.
        """
        root = tmp_path / "demo"
        new_run("demo", root)
        module_run(root, "win")
        function_run(
            root,
            "kaiser_window",
            "win",
            params=[("n", "size_t"), ("beta", "double")],
            out_type="double",
        )
        hdr = root / "native" / "inc" / "win" / "win_core.h"
        text = hdr.read_text(encoding="utf-8")
        decl = re.search(r"^[^\n]*kaiser_window[^\n]*$", text, re.M)
        assert decl, "no kaiser_window declaration in the module header"
        hdr.write_text(
            text[: decl.start()] + _FREE_FN_DOC + text[decl.start() :],
            encoding="utf-8",
        )
        apply_run(root)

        ext_c = (root / "native/src/win/win_ext.c").read_text()
        pyi = (root / "src/demo/win/win.pyi").read_text()
        runtime = _runtime_doc(ext_c, "kaiser_window")
        m = re.search(
            r'def kaiser_window\([^\n]*\n    """(.*?)\n    """', pyi, re.S
        )
        assert m, "no stub docstring for kaiser_window"
        body = m.group(1).split("\n")
        stub = [body[0]] + [
            ln[4:] if ln.startswith("    ") else ln for ln in body[1:]
        ]
        assert [ln for ln in runtime if ln.strip()] == [
            ln for ln in stub if ln.strip()
        ]
        assert "Number of samples in the window." in "\n".join(runtime)

    def test_free_function_doc_is_escaped_into_the_c_literal(self, tmp_path):
        """A quote in a manifest `doc` must not break the build.

        The entry was interpolated bare into `"{doc}"`, so a `"` or a newline
        produced a module that did not compile -- gh-633's class, on the one
        surface it had not reached. It now goes through `_build_ml_doc` like
        every other doc literal.
        """
        root = tmp_path / "demo"
        new_run("demo", root)
        module_run(root, "win")
        function_run(
            root,
            "hann",
            "win",
            doc='Apply the "raised cosine" taper.',
            params=[("n", "size_t")],
            out_type="double",
        )
        ext_c = (root / "native/src/win/win_ext.c").read_text()
        assert r"\"raised cosine\"" in ext_c, (
            "the manifest doc reached the C literal unescaped; a bare quote "
            "closes the string and the module does not compile"
        )

    def test_undocumented_method_keeps_the_synthesised_demo(self, tmp_path):
        """The fallback still fires when the header says nothing."""
        root = tmp_path / "demo"
        new_run("demo", root)
        object_run(
            root,
            "fir",
            None,
            state_vars=[("num_taps", "int", "4")],
            arg_type="void",
            return_type="float",
        )
        method_run(
            root, "fir", "run", None, "float[]", "float", True, [], params=[]
        )
        apply_run(root)
        ext_c = (root / "native/src/fir/fir_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "run"))
        assert ">>> from demo import Fir" in runtime
        assert "Examples" not in runtime


# ── built-ins (gh-700) ──────────────────────────────────────────────────────
#
# gh-642 gave the *authored* methods their full block and left the built-ins
# -- step()/steps()/reset() -- emitting a signature line, the @brief and a
# synthesised demo. Every object has a step, so that was the bulk of the
# runtime surface doppler measured, and why their runtime-incomplete count
# barely moved (914 -> 861) on 0.38.0.
#
# The built-ins are the one place jm supplies real prose of its own, so the
# rule differs from an authored method: the block is rendered only when the
# header says more than a @brief. A bare-@brief component must stay
# byte-identical, or every existing project's generated files churn to say
# the same thing at greater length.

_BUILTIN_DOC = """/**
 * @brief Run a block of looks through the detector.
 *
 * The detector integrates over the whole block, so consecutive calls are
 * equivalent to one call over the concatenation.
 *
 * @param in Input looks, any length.
 * @return The lock metric for this block.
 *
 * @code
 * >>> obj.step(np.zeros(4, dtype=np.float64))
 * 0.0
 * @endcode
 */
"""

_BRIEF_ONLY = """/**
 * @brief Run a block of looks through the detector.
 */
"""


def _detector(tmp_path: Path, block: str | None) -> Path:
    """A standalone object whose built-in step() carries *block*."""
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "det",
        None,
        state_vars=[("thresh", "double", "1.0")],
        arg_type="double[]",
        return_type="double",
    )
    if block is not None:
        hdr = root / "native" / "inc" / "det" / "det_core.h"
        text = hdr.read_text(encoding="utf-8")
        m = re.search(
            r"/\*\*\n(?: \*[^\n]*\n)+ \*/\n(?=[^\n]*\ndet_step\s*\(|det_step\s*\()",
            text,
        )
        assert m, "the scaffold no longer emits a block above det_step"
        hdr.write_text(
            text[: m.start()] + block + text[m.end() :], encoding="utf-8"
        )
    apply_run(root)
    return root


class TestBuiltinStep:
    """The built-in step() must obey the same parity rule as a method."""

    def test_authored_header_reaches_both_faces(self, tmp_path):
        root = _detector(tmp_path, _BUILTIN_DOC)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        pyi = (root / "src/demo/det.pyi").read_text()
        runtime = _runtime_doc(ext_c, "step")
        stub = _stub_doc(pyi, "step")
        # The runtime leads with a signature line the stub does not need.
        assert runtime[0].startswith("step(")
        assert [ln for ln in runtime[2:] if ln.strip()] == [
            ln for ln in stub if ln.strip()
        ]

    def test_authored_prose_reaches_the_runtime(self, tmp_path):
        """The regression: @param/@return/body text in the .so, not just the
        stub."""
        root = _detector(tmp_path, _BUILTIN_DOC)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        rt = "\n".join(_runtime_doc(ext_c, "step"))
        assert "Parameters" in rt
        assert "Input looks, any length." in rt
        assert "The lock metric for this block." in rt
        assert "integrates over the whole block" in rt

    def test_authored_example_replaces_the_synthesised_demo(self, tmp_path):
        root = _detector(tmp_path, _BUILTIN_DOC)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        rt = "\n".join(_runtime_doc(ext_c, "step"))
        assert rt.count("Examples") == 1
        assert ">>> from demo import Det" not in rt

    def test_bare_brief_does_not_grow_a_parameters_section(self, tmp_path):
        """The zero-churn rule -- jm's own canned prose is not 'authored'.

        Rendering the block for a bare @brief would rewrite every existing
        project's generated files to say the same thing at greater length,
        and trip the manifest-drift gate on components nobody documented.
        """
        root = _detector(tmp_path, _BRIEF_ONLY)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        rt = "\n".join(_runtime_doc(ext_c, "step"))
        assert "Run a block of looks through the detector." in rt
        assert "Parameters" not in rt
        assert ">>> from demo import Det" in rt, "lost the synthesised demo"

    def test_undocumented_keeps_the_canned_text(self, tmp_path):
        root = _detector(tmp_path, None)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        rt = "\n".join(_runtime_doc(ext_c, "step"))
        assert "Process an input buffer and return a result." in rt
        assert "Parameters" not in rt


class TestBuiltinReset:
    """reset()'s stub already rendered the block; its runtime stopped at the
    brief."""

    def test_reset_runtime_is_escaped(self, tmp_path):
        """It was interpolated bare into `"{...}"` -- gh-633's class."""
        root = _detector(tmp_path, None)
        ext_c = (root / "native/src/det/det_ext.c").read_text()
        assert '"Reset state to post-create defaults.\\n"' in ext_c
