"""Doctest gate: every synthesized doctest in a generated stub must be a
well-formed, structurally-complete doctest.

This is the in-process half of the "passing examples everywhere" guarantee:
it runs in the pure-Python suite (no compiler) and proves the generator emits
syntactically valid doctests that construct the object before exercising it.
Executing them against a *built* extension is the consumer's job (e.g.
doppler runs ``pytest --doctest-glob='*.pyi'`` after building).
"""

import doctest
import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import extract_doctests  # noqa: E402


def _build_project(dest: Path):
    with contextlib.redirect_stdout(io.StringIO()):
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
        method_run(dest, "mix", "scale", "sig", "float", "float", False, [])
        apply_run(dest)


def test_generated_pyi_doctests_are_wellformed(tmp_path):
    dest = tmp_path / "dsp"
    _build_project(dest)
    pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
        encoding="utf-8"
    )
    blocks = extract_doctests(pyi)
    assert blocks, "no doctests found in generated .pyi"

    parser = doctest.DocTestParser()
    for body in blocks:
        # Parses without raising → balanced prompts, valid want/got blocks.
        examples = parser.get_examples(body)
        assert examples, "doctest block has no >>> examples"
        # A useful example constructs the class before calling it.
        assert any(
            "import" in ex.source or "Mix(" in ex.source for ex in examples
        ), f"doctest does not set up an object:\n{body}"


def test_array_constructor_object_skips_broken_example(tmp_path):
    """An object whose constructor needs an array (rendered `...`) must NOT
    emit a `>>> obj = X(...)` example — that would raise TypeError. The
    Examples block is omitted instead.
    """
    dest = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", dest)
        module_run(dest, "sig")
        # a fixed-length array state field -> constructor arg has no scalar
        # literal, so py_create_args renders as `...`.
        object_run(
            dest,
            "fir",
            module="sig",
            state_vars=[("taps", "float _Complex[64]", "0.0")],
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        apply_run(dest)
    pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
        encoding="utf-8"
    )
    class_doc = pyi.split("class Fir:")[1].split('"""')[1]
    assert ">>> obj = Fir(...)" not in class_doc
    # any doctests that DO remain must be well-formed and not pass an ellipsis
    for body in extract_doctests(pyi):
        for ex in doctest.DocTestParser().get_examples(body):
            assert "(...)" not in ex.source


def test_class_doctest_present_and_constructs(tmp_path):
    dest = tmp_path / "dsp"
    _build_project(dest)
    pyi = (dest / "src" / "dsp" / "sig" / "sig.pyi").read_text(
        encoding="utf-8"
    )
    class_doc = pyi.split("class Mix:")[1].split('"""')[1]
    assert ">>>" in class_doc
    examples = doctest.DocTestParser().get_examples(class_doc)
    assert any("Mix(" in ex.source for ex in examples)
