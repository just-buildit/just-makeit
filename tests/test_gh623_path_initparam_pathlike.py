"""gh-623: an object `init_param` of `type = "path"` annotated bare `str`.

The binding coerces a path with `PyUnicode_FSConverter` (the shared `_coerce`
primitive), which accepts anything `os.fspath()` accepts -- so passing a
`pathlib.Path` works at runtime, and doppler ships a test asserting exactly
that. The stub said `str`, so the tested, documented, idiomatic call was an
error to mypy. A `jm function` param of the same type already rendered
`str | os.PathLike`, which made a single generated file disagree with itself.

The annotation now comes from one constant (`_coerce.PATH_PY_TYPE`) that sits
beside the coercion making it true, so the four generators that render a path
-- the module stub (`_stubs._py`), the standalone template
(`_context._state`), the handle stub (`_handle._pyi_arg_ann`) and the free
function (`_stubs._fn_stub`) -- cannot drift apart again.

The second half of the fix is the import: `os.PathLike` in a signature is an
undefined name unless the stub binds `os`. gh-515 chose the narrow `str`
*specifically* to avoid that import, so widening without emitting it would
trade a wrong annotation for a broken stub.
"""

import ast
import sys
from pathlib import Path

import pytest

# Pinned as a LITERAL, not imported from _coerce: a test that reads the
# constant it is checking passes just as happily when the constant is wrong.
PATHLIKE = "str | os.PathLike"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _bound_names(text: str) -> set[str]:
    """Every top-level name the stub's imports bind."""
    out: set[str] = set()
    for node in ast.parse(text).body:
        if isinstance(node, ast.Import):
            out |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out |= {a.asname or a.name for a in node.names}
    return out


def _assert_stub_is_sound(text: str) -> None:
    """The stub parses, and binds `os` iff it references it.

    This is the invariant that matters: a future path surface that widens its
    annotation without emitting the import fails here rather than shipping a
    stub whose signature names something undefined.
    """
    ast.parse(text)
    if "os.PathLike" in text:
        assert "os" in _bound_names(text), (
            "stub annotates os.PathLike without importing os"
        )


@pytest.fixture()
def standalone(tmp_path):
    """A standalone object whose only ctor param is a path."""
    dest = tmp_path / "dsp"
    new_run("dsp", dest, [], [])
    object_run(dest, "rdr", None, init_params=[("filepath", "path", "")])
    return (dest / "src" / "dsp" / "rdr.pyi").read_text(encoding="utf-8")


@pytest.fixture()
def module_obj(tmp_path):
    """The reported shape: a path init-param on a MODULE object."""
    dest = tmp_path / "dsp"
    new_run("dsp", dest, [], [])
    module_run(dest, "io", ["rdr"])
    object_run(dest, "rdr", "io", init_params=[("filepath", "path", "")])
    return (dest / "src" / "dsp" / "io" / "io.pyi").read_text(encoding="utf-8")


@pytest.fixture()
def no_path(tmp_path):
    """A plain scalar object -- the guard that `import os` stays conditional."""
    dest = tmp_path / "dsp"
    new_run("dsp", dest, [], [])
    object_run(dest, "gain", None, state_vars=[("g", "double", "1.0")])
    return (dest / "src" / "dsp" / "gain.pyi").read_text(encoding="utf-8")


class TestAnnotation:
    def test_module_object_ctor_takes_pathlike(self, module_obj):
        """The reported case: doppler's `Reader(pathlib.Path(...))`."""
        assert f"def __init__(self, filepath: {PATHLIKE})" in module_obj

    def test_standalone_object_ctor_takes_pathlike(self, standalone):
        """The standalone template is a separate generator -- same answer."""
        assert f"def __init__(self, filepath: {PATHLIKE})" in standalone

    @pytest.mark.parametrize("stub", ["standalone", "module_obj"])
    def test_signature_agrees_with_its_own_docstring(self, stub, request):
        """The half-fixed state is its own bug: gh-623 was noticed *because*
        one file disagreed with itself. Both halves must widen together."""
        text = request.getfixturevalue(stub)
        assert f"filepath : {PATHLIKE}" in text  # numpydoc Parameters
        assert f"filepath: {PATHLIKE}" in text  # signature


class TestImport:
    @pytest.mark.parametrize("stub", ["standalone", "module_obj"])
    def test_path_stub_binds_os(self, stub, request):
        text = request.getfixturevalue(stub)
        assert "import os" in text
        _assert_stub_is_sound(text)

    def test_object_without_a_path_does_not_import_os(self, no_path):
        """gh-515's concern was a gratuitous import; it stays conditional, so
        an object with no path renders exactly as before."""
        assert "import os" not in no_path
        _assert_stub_is_sound(no_path)
