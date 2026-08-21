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

import pytest
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
        # gh-1043: shaped like a doctest is not the same as executable.
        # `>>> obj = Sync(marker=0U)` parses as a perfectly well-formed
        # example, so this gate was green on a line Python cannot compile —
        # and for a downstream running `pytest --doctest-glob='*.pyi'` that
        # is a hard COLLECTION error, not a failed comparison.
        for ex in examples:
            try:
                compile(ex.source, "<doctest>", "exec")
            except SyntaxError as exc:
                raise AssertionError(
                    f"generated doctest line is not valid Python: "
                    f"{ex.source.rstrip()!r} ({exc})"
                ) from exc
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


def _example_sources(text: str) -> list[str]:
    """Every `>>> ` example in *text*, continuation lines joined.

    A scan rather than `doctest.DocTestParser`, because the runtime face is a
    C string literal inside `<mod>_ext.c` -- one `"..."` per line, a trailing
    `\n` escape, sometimes a leading ` * ` comment marker -- and is not a
    docstring the parser can be handed. Unwrapping that is the whole point:
    a scan that does not strip the quote reads ZERO examples out of the
    runtime face and the check passes having looked at nothing, which is
    exactly how gh-1043 shipped the same wrong literal to both faces.

    A `...` continuation is joined to the `>>> ` line above it. A long
    constructor example wraps, and reading the first half alone gives
    `obj = Wide(`, which fails to compile for a reason unrelated to the test.
    """
    out: list[str] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        # Unwrap one line of a C string literal, if that is what this is.
        if line.startswith('"'):
            line = line[1:]
        if line.endswith('"'):
            line = line[:-1]
        if line.endswith("\\n"):
            line = line[:-2]
        line = line.rstrip()
        if line.lstrip().startswith(">>> "):
            if current:
                out.append("\n".join(current))
            current = [line.lstrip()[4:]]
        elif line.lstrip().startswith("... ") and current is not None:
            current.append(line.lstrip()[4:])
        elif current is not None:
            out.append("\n".join(current))
            current = None
    if current:
        out.append("\n".join(current))
    return [e for e in out if e.strip() and not e.rstrip().endswith(":")]


#: Every scalar state type jm supports, with a C default spelled the way the
#: C side spells it. gh-1043 was live for months because the gate's own
#: fixture used `float` alone: a check that cannot see an unsigned field
#: cannot fail on `0U`, and a gate that can only fail by coincidence is a
#: description of the code rather than a constraint on it.
_EVERY_SCALAR_STATE = [
    ("u8", "uint8_t", "0U"),
    ("u16", "uint16_t", "0U"),
    ("u32", "uint32_t", "0U"),
    ("u64", "uint64_t", "0U"),
    ("big", "uint64_t", "5ULL"),
    ("n", "size_t", "0"),
    ("i", "int", "0"),
    ("i64", "int64_t", "0"),
    ("f", "float", "1.0f"),
    ("d", "double", "1.0"),
    ("c", "float _Complex", "0.0"),
    ("flag", "bool", "true"),
]


@pytest.mark.parametrize("shape", ["standalone", "module"])
def test_every_scalar_state_type_yields_compilable_examples(tmp_path, shape):
    """gh-1043: the generated example must be Python, not C.

    An unsigned state field put the C literal `0U` into the constructor
    example in BOTH faces -- the `.pyi` and the runtime docstring. `0U` is a
    SyntaxError, and for a downstream running `pytest --doctest-glob='*.pyi'`
    that is a hard collection error rather than a failed comparison.

    Parametrised over BOTH object shapes because jm has two `.pyi`
    generators: a standalone object goes through
    `_context/_types._py_default` and a module-aggregated one through
    `_stubs._py_default_stub`. They are the peer pair that has drifted before
    (`feedback_fix_the_peer`), and gh-1043 was present in both. A gate
    covering one shape passes with the other still broken -- measured, not
    assumed: reverting the standalone half alone left a module-only gate
    green.
    """
    dest = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", dest)
        if shape == "module":
            module_run(dest, "sig")
        object_run(
            dest,
            "wide",
            module="sig" if shape == "module" else None,
            state_vars=_EVERY_SCALAR_STATE,
            arg_type="float",
            return_type="float",
        )
        apply_run(dest)

    if shape == "module":
        stub = dest / "src" / "dsp" / "sig" / "sig.pyi"
        ext = dest / "native" / "src" / "sig" / "sig_ext.c"
    else:
        stub = dest / "src" / "dsp" / "wide.pyi"
        ext = dest / "native" / "src" / "wide" / "wide_ext.c"
    faces = {".pyi": stub, "runtime docstring": ext}
    seen = 0
    for face, path in faces.items():
        text = path.read_text(encoding="utf-8")
        for source in _example_sources(text):
            seen += 1
            try:
                compile(source, "<doctest>", "exec")
            except SyntaxError as exc:
                raise AssertionError(
                    f"{face}: generated example is not valid Python: "
                    f"{source!r} ({exc})"
                ) from exc
    # Never vacuous: if the construction example stopped being emitted, or
    # the C-string unwrapping above stopped matching, this loop would pass
    # having compiled nothing at all.
    assert seen >= 2, f"only {seen} example lines found across both faces"
