"""Verify that every example's README.md is up-to-date with its .steps/ files.

Runs assemble.py --check for each example.  Fails if any README is stale,
so contributors can't add .steps/ content and forget to regenerate.
"""

import importlib.util
from pathlib import Path

import pytest

EXAMPLES_DIR = (
    Path(__file__).parent.parent / "src" / "just_makeit" / "examples"
)


def _discover_assemblers():
    return sorted(EXAMPLES_DIR.glob("*/assemble.py"))


@pytest.mark.parametrize(
    "assemble_py",
    _discover_assemblers(),
    ids=[p.parent.name for p in _discover_assemblers()],
)
def test_readme_assembled(assemble_py):
    spec = importlib.util.spec_from_file_location("_asm", assemble_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = mod.assemble()
    readme = assemble_py.parent / "README.md"
    current = readme.read_text(encoding="utf-8") if readme.exists() else ""
    assert current == result, (
        f"{readme.relative_to(Path(__file__).parent.parent)} is stale — "
        f"run: python3 {assemble_py.relative_to(Path(__file__).parent.parent)}"
    )
