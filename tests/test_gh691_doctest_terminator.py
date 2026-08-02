"""gh-691: a generated `.pyi` example must not swallow its closing quotes.

Under a text-mode stub doctest run (`pytest --doctest-glob='*.pyi'`, which
griffe/mkdocstrings-style consumers and doppler's stub gate use) the `.pyi` is
parsed as a **text file**, and a doctest's expected output runs until the next
blank line. Without a blank line before the closing `\"\"\"`, the last example's
expected output becomes:

    '0\\n\"\"\"\\ndef __init__(self, norm_freq: float = ...) -> None: ...\\n'

which can never match, so the example always fails.

`render_numpy_doc` has emitted that blank line for authored **method** `@code`
blocks all along, with a comment naming this exact hazard. The **class**
`Examples` path added in gh-624 did not follow the precedent, and 0.37.0 shipped
it -- breaking the stub gate on eight doppler modules in one pin bump.

The blank line is therefore load-bearing, not cosmetic, which is why the sweep
below is generic: it asserts the property for every shape rather than only the
one that regressed. Any future emitter that forgets it fails here.
"""

from __future__ import annotations

import doctest
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CLASS_CODE = """
 * @brief Numerically controlled oscillator.
 *
 * @code
 * >>> from demo.source import Nco
 * >>> nco = Nco(norm_freq=0.25)
 * >>> nco.get_phase_inc()
 * 0
 * @endcode"""

_METHOD_CODE = """ * @brief Retune the oscillator.
 *
 * @code
 * >>> obj.tune(0.5)
 * @endcode"""


def _author(root: Path, old: str, new: str) -> None:
    h = root / "native" / "inc" / "nco" / "nco_core.h"
    t = h.read_text(encoding="utf-8")
    assert old in t, f"the scaffold no longer writes {old!r}"
    h.write_text(t.replace(old, new, 1), encoding="utf-8")


def _module_project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "source")
    object_run(
        root,
        "nco",
        "source",
        state_vars=[("phase_inc", "int", "0")],
        init_params=[("norm_freq", "double", "0.25")],
        arg_type="void",
        return_type="float",
    )
    return root


def _standalone_project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "nco",
        None,
        state_vars=[("phase_inc", "int", "0")],
        arg_type="void",
        return_type="float",
    )
    return root


def _authored_class(tmp_path: Path) -> Path:
    """The shape that regressed: a `@code` on create() (gh-624)."""
    root = _module_project(tmp_path)
    _author(root, " * @brief Create a nco instance.", _CLASS_CODE)
    apply_run(root)
    return root


def _authored_method(tmp_path: Path) -> Path:
    root = _standalone_project(tmp_path)
    method_run(
        root,
        "nco",
        "tune",
        None,
        "double",
        "void",
        False,
        [],
        params=[("hz", "double")],
    )
    _author(root, " * @brief tune.", _METHOD_CODE)
    apply_run(root)
    return root


def _synthesised(tmp_path: Path) -> Path:
    root = _standalone_project(tmp_path)
    apply_run(root)
    return root


SHAPES = {
    "authored_class_code": _authored_class,
    "authored_method_code": _authored_method,
    "synthesised_demo": _synthesised,
}


def _offenders(pyi: str) -> list[str]:
    """Examples whose expected output swallowed the closing quotes."""
    return [
        e.source.strip()
        for e in doctest.DocTestParser().get_examples(pyi)
        if '"""' in e.want
    ]


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
def test_no_example_swallows_its_terminator(tmp_path, shape):
    root = SHAPES[shape](tmp_path)
    for pyi_path in root.rglob("*.pyi"):
        text = pyi_path.read_text(encoding="utf-8")
        bad = _offenders(text)
        assert not bad, (
            f"{pyi_path.name}: {len(bad)} example(s) swallow the closing "
            f'\'"""\' into their expected output, so they can never match '
            f"under `pytest --doctest-glob='*.pyi'`. Emit a blank line before "
            f"the closing quotes. Offending example(s): {bad}"
        )


def test_the_regressed_shape_actually_has_examples(tmp_path):
    """Guard the guard: a shape with no examples would pass vacuously."""
    root = _authored_class(tmp_path)
    text = next(root.rglob("*.pyi")).read_text(encoding="utf-8")
    assert doctest.DocTestParser().get_examples(text), (
        "this shape stopped emitting examples, so the sweep above proves "
        "nothing for it"
    )


def test_authored_class_example_is_blank_line_terminated(tmp_path):
    root = _authored_class(tmp_path)
    text = next(root.rglob("*.pyi")).read_text(encoding="utf-8")
    body = text.split("class Nco")[1].split('"""')[1]
    # ...last output line, then a BLANK line, then the closing quotes' indent.
    assert body.rstrip(" ").endswith("\n\n"), (
        "the class docstring must carry a blank line between its last example "
        f"and its closing quotes; tail is {body[-30:]!r}"
    )
