"""gh-530: an object with both `[[state]]` and `[[init_params]]` produced a
module-aggregated stub whose `__init__` signature disagreed with its own
docstring.

The runtime constructor is init_params-based whenever both are declared -- the
gh-69 contract: init_params drive `create()`, and scalar state stays internal
(set from defaults, reachable only through generated getters/setters). The
`.pyi` docstring's Parameters block already documented the init_params. But the
module stub's `def __init__` line was built from the state vars, because its
branch order checked `state_vars and not no_state` before init_params -- the
reverse of the docstring builder. So the two halves of the same stub named
different constructor arguments.

The standalone `<obj>.pyi` never had this: `make_state_ctx` overrides its
signature slot with the init_params one (the gh-69 machinery). Only the
module-aggregated `_stubs._obj_stub` peer lagged. The fix reorders its branches
to give init_params precedence, matching both the runtime and the docstring.

Verified against runtime: the module `Rdr_init` parses the init_params and
calls `rdr_create(<init_params>)`, ignoring the state vars as constructor
arguments -- so the fixed signature is the one that agrees with what the C
actually accepts.
"""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _pyi_class_block(pyi_text: str, cls: str) -> str:
    """The lines from `class <cls>:` up to the next top-level `class`."""
    out, capturing = [], False
    for line in pyi_text.splitlines():
        if line.startswith(f"class {cls}"):
            capturing = True
        elif capturing and line.startswith("class "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out)


class TestModuleStubBothDeclared:
    """state + init_params on a MODULE object -- the reported case."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [])
        module_run(dest, "io", ["rdr"])
        object_run(
            dest,
            "rdr",
            module="io",
            state_vars=[("cap", "size_t", "16")],
            init_params=[("filepath", "path", "")],
        )
        return dest

    def test_signature_lists_init_params_not_state(self, project):
        pyi = (project / "src" / "dsp" / "io" / "io.pyi").read_text()
        block = _pyi_class_block(pyi, "Rdr")
        assert "def __init__(self, filepath: str) -> None: ..." in block
        # The bug: the state var leaked into the ctor signature.
        assert "def __init__(self, cap:" not in block

    def test_signature_agrees_with_docstring(self, project):
        """The two halves of the same stub must name the same parameter."""
        pyi = (project / "src" / "dsp" / "io" / "io.pyi").read_text()
        block = _pyi_class_block(pyi, "Rdr")
        assert "filepath : str" in block  # docstring Parameters
        assert "filepath: str" in block.split("def __init__")[1]  # signature

    def test_signature_matches_runtime_ctor(self, project):
        """The C `Rdr_init` parses init_params and calls
        `rdr_create(<init_params>)` -- the stub must match what runs."""
        c = (project / "native" / "src" / "io" / "io_ext_rdr.c").read_text()
        assert '{"filepath", NULL}' in c
        assert "rdr_create(PyBytes_AS_STRING(filepath))" in c
        assert '{"cap", NULL}' not in c

    def test_stub_parses(self, project):
        ast.parse((project / "src" / "dsp" / "io" / "io.pyi").read_text())


class TestStateOnlyStillWorks:
    """The reorder must not regress the common state-only ctor."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [])
        module_run(dest, "io", ["osc"])
        object_run(
            dest, "osc", module="io", state_vars=[("gain", "double", "1.0")]
        )
        return dest

    def test_state_vars_are_the_ctor(self, project):
        block = _pyi_class_block(
            (project / "src" / "dsp" / "io" / "io.pyi").read_text(), "Osc"
        )
        assert "def __init__(self, gain: float = ...) -> None: ..." in block


class TestNoStateInitParamsUnchanged:
    """`no_state=true` + init_params already worked (the coherent pairing);
    it must be byte-identical after the reorder."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, [], [])
        module_run(dest, "io", ["rdr"])
        object_run(
            dest,
            "rdr",
            module="io",
            no_state=True,
            init_params=[("filepath", "path", "")],
        )
        return dest

    def test_ctor_is_init_params(self, project):
        block = _pyi_class_block(
            (project / "src" / "dsp" / "io" / "io.pyi").read_text(), "Rdr"
        )
        assert "def __init__(self, filepath: str) -> None: ..." in block


class TestStandaloneWasNeverBroken:
    """The standalone `<obj>.pyi` already agreed -- guard it stays that way."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["rdr"], [("gain", "double", "1.0")])
        return dest

    def test_standalone_state_only_ctor(self, project):
        # The standalone generator annotates a state var with its numpy scalar
        # type (np.float64), where the module generator uses plain `float` --
        # a pre-existing cross-generator difference, unrelated to gh-530. What
        # matters here is only that the standalone ctor is still state-based
        # and untouched by the module-path reorder.
        block = _pyi_class_block(
            (project / "src" / "dsp" / "rdr.pyi").read_text(), "Rdr"
        )
        sig = block.split("def __init__")[1].split("\n")[0]
        assert "gain: np.float64" in sig
        assert "filepath" not in sig
