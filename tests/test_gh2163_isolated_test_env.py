"""gh-2163: the isolated test environment imports only what it declares.

`PYTEST_ISOLATED` is `uv run --no-project`, which is meant to keep the dev
group out of the suite. It does not: uv still discovers an interpreter from the
cwd, and from a checkout that is the dev `.venv` above it. The ephemeral
`--with` env is layered over that venv, so a dev-group package (mypy, say)
imports on a laptop and fails in CI. A test that needs one passes locally and
breaks on CI, which is the single-environment blind spot gh-1442 was about.

GATE: from the checkout, where a dev `.venv` sits above the cwd, the isolated
interpreter cannot import mypy (a dev-group-only package). The pin
`--python $(BASE_PYTHON)` in the Makefile is what makes that true.

CI has no dev `.venv`, so the leak cannot occur there and this gate skips with
that reason. It runs wherever the leak can, which is every checkout a developer
uses.

Sabotage proof: removing the `--python` pin from PYTEST_ISOLATED makes the
probe print True, and this gate goes red.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(
    not (REPO / ".venv").is_dir(),
    reason="no dev .venv above the checkout, so the leak cannot occur here",
)
@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_the_isolated_suite_cannot_import_a_dev_group_package() -> None:
    # The probe rule lives here, not in the Makefile: a rule the Makefile
    # defines is a shared target, and the standard gates refuse one. The
    # Makefile is included, so the probe runs the very PYTEST_ISOLATED command.
    probe = (
        "include Makefile\n"
        "isolation-probe:\n"
        '\t@$(PYTEST_ISOLATED) python -c "import importlib.util as u; '
        "print(u.find_spec('mypy') is not None)\"\n"
    )
    r = subprocess.run(
        ["make", "-s", "-f", "-", "isolation-probe"],
        cwd=str(REPO),
        input=probe,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "False", (
        "the isolated suite imports mypy, a dev-group package: the "
        "--python pin in PYTEST_ISOLATED is missing or not holding"
    )
