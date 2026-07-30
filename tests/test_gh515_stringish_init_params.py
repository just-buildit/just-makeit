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


# ── defect 1: type = "path" in object init_params (gh-515) ─────────────────
#
# ``path`` is a pseudo-type deliberately absent from ``_CTYPE_META``. Method
# and function params learned it in gh-353; object init_params never did, so
# ``jm apply`` died with ``KeyError: 'path'``. The coercion primitives are
# shared (``_coerce``), so the generated glue must match the handle/function
# shape exactly — including the gh-219 rule that the borrowed ``PyBytes`` is
# released only AFTER ``create()`` has copied the string.


def test_path_init_param_applies(tmp_path):
    """The headline crash: apply must complete for ``type = "path"``."""
    pyi = _project_with_init_param(tmp_path / "p", "path")
    assert pyi.is_file()


def test_path_init_param_ext_c_coercion(tmp_path):
    """The binding uses ``PyUnicode_FSConverter`` and releases after create."""
    root = tmp_path / "p"
    _project_with_init_param(root, "path")
    ext = (root / "native" / "src" / "rdr" / "rdr_ext.c").read_text(
        encoding="utf-8"
    )

    assert "PyObject *path = NULL;" in ext
    assert '"O&|s"' in ext
    assert "PyUnicode_FSConverter, &path" in ext
    assert "PyBytes_AS_STRING(path)" in ext

    # gh-219: the release must follow the create() call, never precede it.
    create_at = ext.index("self->handle = rdr_create(")
    release_at = ext.index("Py_XDECREF(path);", create_at)
    assert release_at > create_at

    # ...and both pre-call error paths free the borrow too.
    parse_fail = ext.index("PyArg_ParseTupleAndKeywords")
    assert "Py_XDECREF(path);" in ext[parse_fail:create_at]
    enum_fail = ext.index("sample_type must be one of")
    assert "Py_XDECREF(path);" in ext[enum_fail:create_at]


def test_path_init_param_core_h_signature(tmp_path):
    """C sees a plain ``const char *`` it is told to copy."""
    root = tmp_path / "p"
    _project_with_init_param(root, "path")
    hdr = (root / "native" / "inc" / "rdr" / "rdr_core.h").read_text(
        encoding="utf-8"
    )
    assert "rdr_create(const char *path, int sample_type)" in hdr
    # The zero-seeded smoke/bench create passes NULL for the path.
    assert "rdr_create(NULL, 0)" in hdr


def test_path_init_param_pyi(tmp_path):
    """The stub parses and types the path as a required ``str | os.PathLike``.

    gh-623: the binding coerces with ``PyUnicode_FSConverter``, so a
    ``pathlib.Path`` is as valid as a ``str`` — annotating bare ``str`` made a
    working call a type error. The widened annotation drags in ``import os``,
    which the stub must therefore carry."""
    pyi = _project_with_init_param(tmp_path / "p", "path")
    text = pyi.read_text(encoding="utf-8")

    ast.parse(text)

    # Required-positional: no `= ...` default, and ahead of the enum kwarg.
    assert (
        "def __init__(self, path: str | os.PathLike, sample_type: str" in text
    )
    assert "path : str | os.PathLike\n" in text
    # The annotation names `os`, so the stub must bind it (gh-623).
    assert "import os" in text
    # jm cannot invent a real path, so no construction example is emitted.
    assert "Rdr(...)" not in text


def test_path_init_param_tests_are_skipped(tmp_path):
    """gh-273 machinery treats a path ctor as unseedable, not as a failure."""
    root = tmp_path / "p"
    _project_with_init_param(root, "path")
    smoke = (root / "native" / "tests" / "test_rdr_core.c").read_text(
        encoding="utf-8"
    )
    assert "SKIPPED" in smoke
    pytest_py = (root / "src" / "probe" / "tests" / "test_rdr.py").read_text(
        encoding="utf-8"
    )
    assert "skipTest" in pytest_py


def test_path_with_optional_array_is_rejected(tmp_path, capsys):
    """Combining a path with an optional-array ctor must error, not leak.

    The optional-array/dtype-dispatch paths emit ``create()`` several times,
    each inside its own brace scope, so there is no single site at which the
    gh-219 release can be placed. jm refuses rather than generate a leak.
    """
    root = tmp_path / "p"
    new_run("probe", root)
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + """
[rdr]
no_state = "true"
no_step  = "true"
class_name = "Rdr"

[[rdr.init_params]]
name = "path"
type = "path"

[[rdr.init_params]]
name = "taps"
type = "float[]"
optional = "true"
create_fn = "rdr_create_taps"
""",
        encoding="utf-8",
    )
    # _apply turns the generator's ValueError into the CLI's `error: …` + exit 1.
    with pytest.raises(SystemExit):
        apply_run(root)
    assert "cannot be combined" in capsys.readouterr().err


# ── PyArg argument order must track the format string ──────────────────────
#
# required_fmt is built from required_entries (TOML declaration order), but
# parse_args used to hoist every array ahead of the loop. The two then
# disagreed whenever a required scalar or path was declared before an array,
# and PyArg wrote each value through the wrong pointer. Found while reviewing
# gh-515; the scalar case predates it.


def _apply_manifest(root: Path, body: str) -> Path:
    new_run("probe", root)
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + body, encoding="utf-8"
    )
    apply_run(root)
    return root / "native" / "src" / "rdr" / "rdr_ext.c"


def test_required_scalar_declared_before_array_parses_in_order(tmp_path):
    """`"iO"` must be fed (&scalar, &array_obj), not (&array_obj, &scalar).

    With the arguments swapped PyArg stored an int through a ``PyObject *``
    and an object pointer through an ``int`` — memory corruption, not a
    diagnosable error. No path param is involved: this is the pre-existing
    form of the defect.
    """
    ext = _apply_manifest(
        tmp_path / "p",
        """
[rdr]
no_state = "true"
no_step  = "true"
class_name = "Rdr"

[[rdr.init_params]]
name = "rate"
type = "int"
required = "true"

[[rdr.init_params]]
name = "taps"
type = "float[]"
""",
    ).read_text(encoding="utf-8")

    assert '"iO"' in ext
    assert "&rate, &taps_obj" in ext


def test_path_declared_before_array_parses_in_order(tmp_path):
    """The path form of the same defect, which is strictly more dangerous.

    ``O&`` consumes *two* varargs (converter, target). Mis-ordered, PyArg read
    the address of ``taps_obj`` as the converter function pointer and called
    it — undefined behaviour rather than a bad store.
    """
    ext = _apply_manifest(
        tmp_path / "p",
        """
[rdr]
no_state = "true"
no_step  = "true"
class_name = "Rdr"

[[rdr.init_params]]
name = "path"
type = "path"

[[rdr.init_params]]
name = "taps"
type = "float[]"
""",
    ).read_text(encoding="utf-8")

    assert '"O&O"' in ext
    assert "PyUnicode_FSConverter, &path, &taps_obj" in ext

    # gh-219: array coercion can fail after the path borrow exists, so that
    # bailout must release it rather than leak the PyBytes.
    assert "if (!taps_arr) { Py_XDECREF(path); return -1; }" in ext
