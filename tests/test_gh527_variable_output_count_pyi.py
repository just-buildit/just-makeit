"""gh-527: a variable_output generator's stub must declare its `count` param.

A `variable_output` method with no input to size from is the *generator* shape:
the binding emits `Py_ssize_t n = 1` and binds it as the leading `count`
(``kwlist {"count", "out"}`` when an ``out=`` is offered, a positional ``"|n"``
otherwise). The stub omitted it entirely, so the two disagreed:

    .pyi     def run(self, out=None) -> NDArray[complex64]
    _ext.c   static char *_kwlist[] = {"count", "out", NULL};   /* "|nO" */

``obj.run(4)`` and ``obj.run(count=7)`` are both accepted at runtime, and
neither type-checked. That is the silent direction of wrong — nothing fails
until a type-checker is pointed at it, and the natural reading is that the
caller is at fault. It was live in shipped stubs rather than local drift.

jm has two stub generators — the standalone-object path in
``_context/_methods.py`` and the module-aggregated path in ``_stubs.py`` — and
they are peers, so both carry the rule and both are pinned here.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run


def _params(pyi_text: str, fn: str) -> list[str]:
    """Positional parameter names of `fn` in a .pyi, via a real parse."""
    tree = ast.parse(pyi_text)  # a stub that does not parse is not a stub
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            return [a.arg for a in node.args.args]
    raise AssertionError(f"{fn} not found in stub")


def _project(root: Path, body: str, pkg: str = "probe") -> Path:
    new_run(pkg, root)
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + body, encoding="utf-8"
    )
    apply_run(root)
    return root


_GENERATOR = """
[lo]
arg_type = "void"
return_type = "float _Complex"
no_step = "true"

[[lo.methods]]
name = "run"
arg_type = "void"
return_type = "float _Complex"
variable_output = true
"""


def test_generator_stub_declares_count_before_out(tmp_path):
    """The headline: `count` is present and precedes `out`.

    Order matters — it must match the binding's kwlist, or a positional
    ``run(4)`` binds to the wrong slot.
    """
    root = _project(tmp_path / "p", _GENERATOR)
    text = (root / "src" / "probe" / "lo.pyi").read_text(encoding="utf-8")
    params = _params(text, "run")

    assert "count" in params, f"count missing from stub: {params}"
    assert "out" in params
    assert params.index("count") < params.index("out")
    assert params[0] == "self"


def test_generator_stub_matches_the_binding_kwlist(tmp_path):
    """The stub and the C it documents must agree, which is the actual bug."""
    root = _project(tmp_path / "p", _GENERATOR)
    ext = (root / "native" / "src" / "lo" / "lo_ext.c").read_text(
        encoding="utf-8"
    )
    assert '_kwlist[] = {"count", "out", NULL}' in ext

    params = _params(
        (root / "src" / "probe" / "lo.pyi").read_text(encoding="utf-8"), "run"
    )
    assert [p for p in params if p != "self"] == ["count", "out"]


def test_count_default_is_one(tmp_path):
    """The binding seeds `Py_ssize_t n = 1`, so the stub default matches."""
    root = _project(tmp_path / "p", _GENERATOR)
    text = (root / "src" / "probe" / "lo.pyi").read_text(encoding="utf-8")
    assert "count: int = 1" in text


def test_array_input_variable_output_has_no_count(tmp_path):
    """A method sized from its input array takes no count — only `out`.

    The renderer derives the length from `PyArray_SIZE`, so inventing a
    `count` here would document a parameter the binding does not accept.
    """
    root = _project(
        tmp_path / "p",
        """
[fir]
arg_type = "float _Complex"
return_type = "float _Complex"

[[fir.methods]]
name = "process"
arg_type = "float _Complex"
return_type = "float _Complex"
variable_output = true
""",
    )
    text = (root / "src" / "probe" / "fir.pyi").read_text(encoding="utf-8")
    params = _params(text, "process")
    assert "count" not in params, f"count wrongly added: {params}"
    assert "x" in params and "out" in params
