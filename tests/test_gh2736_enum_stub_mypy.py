"""gh-2736: mypy --strict reads every enum-backed stub jm writes.

The mypy face of ``test_gh2736_2737_method_enum_params.py``. Split out
because it needs mypy, a dev group tool, so it runs on the
PROJECT_ENV_TESTS path (Makefile), as ``test_gh1724_array_stub_mypy.py``
does; mypy is imported unconditionally, so an absent one fails loudly.

The review of the gh-2736 PR found the standalone producer writing
``mode: Literal["fast", "hq"] = ""`` for a ``string_enum:`` init param
with no default: ``Literal`` made the empty-string default a type error
(``Incompatible default for parameter``), where ``str`` had hidden it.
Text asserts check the spelling; this checks that the spelling is a
stub a type checker accepts -- each producer, each declaration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mypy import api as mypy_api

sys.path.insert(0, str(Path(__file__).parent))

from test_gh2736_2737_method_enum_params import (  # noqa: E402
    INIT_CASES,
    MPDU,
    PKG,
    SET_DEN,
    _project,
    init_enum_project,
)
from _jmrun import run_cli  # noqa: E402


def _strict(root: Path, rels: "list[str]", tmp: Path) -> str:
    """mypy --strict's report on the stubs *rels* under *root*."""
    out, err, _ = mypy_api.run(
        [
            "--strict",
            "--no-incremental",
            "--cache-dir",
            str(tmp / ".mypy_cache"),
            *(str(root / r) for r in rels),
        ]
    )
    # Armed: mypy read the files; a usage error prints neither line.
    assert "Success" in out or "Found" in out, out + err
    return out


@pytest.mark.parametrize("spec", sorted(INIT_CASES))
def test_an_init_param_stub_type_checks(tmp_path, spec):
    root = init_enum_project(tmp_path, spec)
    out = _strict(root, [f"src/{PKG}/so.pyi", f"src/{PKG}/m/m.pyi"], tmp_path)
    assert "Success" in out, out


def test_a_method_and_function_param_stub_type_checks(tmp_path):
    root = _project(tmp_path, [MPDU, SET_DEN])
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    out = _strict(root, [f"src/{PKG}/s.pyi", f"src/{PKG}/m/m.pyi"], tmp_path)
    assert "Success" in out, out
