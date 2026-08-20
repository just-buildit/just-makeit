"""gh-1042: every parameter in the signature gets a `Parameters` entry.

A `variable_output` method with `arg_type = "void"` -- the generator shape --
takes a synthesized `count` and an optional `out=`, and **no `Parameters`
entry was rendered for either**, on either face. With no other parameter, that
left a two-argument signature above no `Parameters` section at all. doppler
found it on `ReedSolomon.generator`; `LO.steps`, `NCO.steps` and `AWGN.steps`
sat the same way.

The exclusion was deliberate and global, not a path the void-arg case failed
to reach -- `_stub_params` still states the rule it came from, that the
section "documents what the algorithm takes". So `out=` was undocumented on
*every* method. Measured on doppler: 81 signatures took `out=` and 6
documented it; 16 took `count` and 1 did.

**The part that made it unfixable downstream:** the header's `@param` entries
are filtered through the Python-facing list before being read, so an authored
`@param count` was **silently discarded**. Not a missing default -- data loss,
with no warning. That is why the reporter concluded no authoring move helped.

Fixed by putting the binding arguments in that list, which makes them both
documented by default and documentABLE. Precedence is header > jm's per-name
default > the generic fallback.

Documented on every shape rather than only where the section would otherwise
be empty: a parameter that appears or vanishes depending on whether a sibling
exists is the caveat-shaped rule, and "every parameter in the signature has an
entry" is the one with no exceptions.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import DoxyBlock, render_numpy_doc  # noqa: E402
from just_makeit._gluedoc import binding_param_docs  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _stub(root: Path, rel: str, name: str) -> str:
    text = (root / rel).read_text(encoding="utf-8")
    m = re.search(
        rf"    def {name}\(.*?\n        \"\"\"(.*?)\n        \"\"\"",
        text,
        re.S,
    )
    assert m, f"no stub docstring for {name}() in {rel}"
    return m.group(1)


def _runtime(root: Path, rel: str, name: str) -> str:
    text = (root / rel).read_text(encoding="utf-8")
    i = text.index(f'{{"{name}",')
    out: list[str] = []
    for raw in text[i:].splitlines()[1:]:
        m = _C_DOC_LINE.match(raw)
        if not m:
            break
        out.append(m.group(1))
    assert out, f"no doc literal for {name}()"
    return "".join(out).encode().decode("unicode_escape")


def _project(tmp_path: Path, module: str | None = None) -> Path:
    """An object with the generator shape and a bulk-input sibling."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "rs",
        module,
        state_vars=[("nroots", "int", "0")],
        arg_type="void",
        return_type="uint8_t",
    )
    for name, arg in (("generator", "void"), ("encode", "uint8_t")):
        _quiet(
            method_run,
            root,
            "rs",
            name,
            module,
            arg,
            "uint8_t",
            True,
            [],
        )
    return root


def _author(root: Path, replace: str) -> None:
    """Put an authored Doxygen block on generator()'s declaration."""
    hdr = next((root / "native" / "inc").rglob("rs_core.h"))
    t = hdr.read_text(encoding="utf-8")
    assert " * @brief generator." in t, "the scaffold no longer seeds @brief"
    hdr.write_text(t.replace(" * @brief generator.", replace, 1), "utf-8")
    _quiet(apply_run, root)


class TestTheGeneratorShape:
    """A signature of nothing but binding arguments."""

    def test_the_section_exists_at_all(self, tmp_path):
        doc = _stub(_project(tmp_path), "src/demo/rs.pyi", "generator")
        assert "Parameters" in doc, doc

    def test_both_arguments_are_documented(self, tmp_path):
        doc = _stub(_project(tmp_path), "src/demo/rs.pyi", "generator")
        assert "count : int" in doc
        assert "out : NDArray[np.uint8] | None" in doc
        # ...and with jm's own text, not the generic "Input." fallback. The
        # entry existing is not the same as the entry saying something true:
        # `out : ndarray | None` described as "Input." is worse than absent.
        assert "How many output samples to ask for" in doc
        assert "Optional pre-allocated output buffer" in doc
        assert "Input." not in doc

    def test_the_runtime_face_agrees(self, tmp_path):
        """gh-642's invariant: the two faces are one text."""
        root = _project(tmp_path)
        rt = _runtime(root, "native/src/rs/rs_ext.c", "generator")
        for line in ("count : int", "out : NDArray[np.uint8] | None"):
            assert line in rt, rt

    def test_the_module_aggregated_stub_agrees(self, tmp_path):
        """The peer generator in `_stubs.py`.

        doppler's ReedSolomon is a MODULE object, so this is the file its
        report was actually about -- fixing only `_context/_methods` would
        have left the reported surface untouched.

        Authored, because this face answers a *different* question for an
        undocumented member: the aggregated `.pyi` collapses it to a one-line
        stub by design, where the standalone one keeps its section skeleton.
        A documented member is the case the report is about.
        """
        root = _project(tmp_path, module="cod")
        _author(
            root,
            " * @brief Build the generator polynomial.\n *\n"
            " * @return The nroots + 1 coefficients.",
        )
        doc = _stub(root, "src/demo/cod/cod.pyi", "generator")
        assert "count : int" in doc
        assert "out : NDArray[np.uint8] | None" in doc


class TestAuthoringReachesIt:
    """The discard: a documented parameter must not vanish."""

    _AUTHORED = (
        " * @brief Build the generator polynomial.\n"
        " *\n"
        " * @param count How many coefficients to return; all of them\n"
        " *              are returned regardless.\n"
        " * @param out A buffer you own, sized by generator_max_out().\n"
        " * @return The nroots + 1 coefficients."
    )

    def test_an_authored_count_reaches_the_stub(self, tmp_path):
        root = _project(tmp_path)
        _author(root, self._AUTHORED)
        doc = _stub(root, "src/demo/rs.pyi", "generator")
        assert "How many coefficients to return" in doc

    def test_an_authored_out_reaches_the_stub(self, tmp_path):
        root = _project(tmp_path)
        _author(root, self._AUTHORED)
        assert "A buffer you own" in _stub(
            root, "src/demo/rs.pyi", "generator"
        )

    def test_the_author_outranks_jms_default(self, tmp_path):
        """Both must not appear; jm's text is a default, not a preamble."""
        root = _project(tmp_path)
        _author(root, self._AUTHORED)
        doc = _stub(root, "src/demo/rs.pyi", "generator")
        assert "How many output samples to ask for" not in doc

    def test_it_reaches_the_runtime_face_too(self, tmp_path):
        root = _project(tmp_path)
        _author(root, self._AUTHORED)
        rt = _runtime(root, "native/src/rs/rs_ext.c", "generator")
        assert "How many coefficients to return" in rt


class TestTheRuleHasNoExceptions:
    """`out=` is documented wherever it is offered, not only when alone."""

    def test_a_bulk_input_method_documents_out_as_well_as_x(self, tmp_path):
        doc = _stub(_project(tmp_path), "src/demo/rs.pyi", "encode")
        assert "x : " in doc
        assert "out : NDArray[np.uint8] | None" in doc

    def test_a_method_offered_neither_gains_neither(self, tmp_path):
        """The guard against documenting arguments that do not exist."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "amp",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        _quiet(
            method_run,
            root,
            "amp",
            "scale",
            None,
            "float",
            "float",
            False,
            [],
        )
        doc = _stub(root, "src/demo/amp.pyi", "scale")
        assert "count : " not in doc
        assert "out : " not in doc


class TestPrecedence:
    """Unit-level, so a break localises to the renderer."""

    def test_defaults_fill_only_what_the_header_omits(self):
        block = DoxyBlock(brief="Go.", params=[("count", "Authored.")])
        out = "\n".join(
            render_numpy_doc(
                block,
                "go",
                [("count", "int"), ("out", "ndarray | None")],
                "ndarray",
                param_defaults=binding_param_docs(),
            )
        )
        assert "Authored." in out
        assert "How many output samples to ask for" not in out
        assert "Optional pre-allocated output buffer" in out

    def test_the_generic_fallback_still_applies_to_other_names(self):
        out = "\n".join(
            render_numpy_doc(
                None,
                "go",
                [("x", "ndarray")],
                "ndarray",
                skeleton_fallback=True,
                param_defaults=binding_param_docs(),
            )
        )
        assert "Input." in out

    def test_binding_param_docs_covers_both_names(self):
        assert set(binding_param_docs()) == {"count", "out"}
        assert all(v.strip() for v in binding_param_docs().values())
