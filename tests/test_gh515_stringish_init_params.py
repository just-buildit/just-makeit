"""gh-515: string-ish object init_params must not break the generated stub.

Two defects shared one root — a scalar init-param with no default fell through
to a branch that could not form a Python literal, and the empty string it
returned was spliced straight into the ``.pyi``:

* ``def __init__(self, path: str = ) -> None: ...`` — a SyntaxError that makes
  the whole stub unparseable, breaking any downstream ``mypy`` run or
  ``pytest --doctest-glob='*.pyi'`` sweep (doppler runs exactly that in CI);
* ``path : str, default`` — a numpydoc entry with a dangling, empty default.

The fix routes both through the ``...`` sentinel the stub machinery already
understands, and drops the ``, default …`` clause when there is no default.
"""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._context._types import _py_default
from just_makeit._new import run as new_run
from just_makeit._stubs import _py_default_stub


# ── unit: the two peer default-renderers agree on the sentinel ──────────────


@pytest.mark.parametrize("ctype", ["const char *", "int", "size_t"])
def test_absent_default_renders_as_sentinel(ctype):
    """A type that cannot synthesise a zero literal yields ``...``, not "".

    The empty string was the actual defect: spliced into a stub signature it
    produced ``x: int = `` rather than a valid default expression.
    """
    assert _py_default(ctype, "") == "..."
    assert _py_default_stub(ctype, "") == "..."


@pytest.mark.parametrize(
    ("ctype", "expected"),
    [("double", ".0"), ("float", ".0"), ("float _Complex", "0j")],
)
def test_float_and_complex_zero_literals_unchanged(ctype, expected):
    """Branches that already synthesise a valid literal stay byte-identical.

    These mirror the C side's zero-seed and construct fine, so gh-515 must not
    churn them into the sentinel and suppress their working examples.
    """
    assert _py_default(ctype, "") == expected


def test_explicit_default_still_passes_through():
    """A real default is untouched — the sentinel is only for absent ones."""
    assert _py_default("const char *", '"/dev/null"') == '"/dev/null"'
    assert _py_default("int", "7") == "7"
    assert _py_default("const char *", "NULL") == '""'


# ── integration: the generated .pyi is valid Python ─────────────────────────


def _project_with_init_param(root: Path, ptype: str) -> Path:
    """Scaffold a --no-state object whose init-param has no default."""
    new_run("probe", root)
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'''
[rdr]
no_state = "true"
no_step  = "true"
class_name = "Rdr"

[[rdr.init_params]]
name = "path"
type = "{ptype}"

[[rdr.init_params]]
name = "sample_type"
type = "string_enum:cf32,cf64,ci32,ci16,ci8"
default = "cf32"
''',
        encoding="utf-8",
    )
    apply_run(root)
    return root / "src" / "probe" / "rdr.pyi"


def test_const_char_init_param_stub_parses(tmp_path):
    """The headline regression: the emitted stub must be valid Python."""
    pyi = _project_with_init_param(tmp_path / "p", "const char *")
    text = pyi.read_text(encoding="utf-8")

    ast.parse(text)  # raised SyntaxError before the fix

    assert "path: str = ..." in text
    # No dangling default clause in the numpydoc block.
    assert "default \n" not in text
    assert "path : str\n" in text


def test_no_construction_example_when_unseedable(tmp_path):
    """An unseedable ctor gets no doctest rather than a failing one.

    ``Rdr(...)`` would hand the constructor an Ellipsis and raise, which is
    exactly what the gated ``--doctest-glob='*.pyi'`` run would catch.
    """
    pyi = _project_with_init_param(tmp_path / "p", "const char *")
    text = pyi.read_text(encoding="utf-8")
    assert "Rdr(...)" not in text
