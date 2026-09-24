"""The CI jm generates for a project is pinned the way jm's own CI is.

GATE: every action the generated CI pins matches jm's own workflows, and it tests jm's Python range.

``templates/ci/github.yml`` is the workflow ``jm ci`` writes into every
scaffolded project. It pinned ``actions/checkout@v4`` and
``actions/setup-python@v5`` -- both ``node20``, which GitHub now forces onto
Node 24 with a deprecation notice on every run -- and its matrix stopped at
3.13 while jm itself supported 3.14. jm's own pins had moved on long before.

Dependabot cannot see this file: it scans ``.github/workflows/`` and nothing
else, so a template is exactly where a pin goes stale unwatched. Rather than
keep a second list current, both checks here DERIVE the answer:

- each action the template uses must appear in jm's own workflows at the
  same ref, so the template follows jm's pins (which Dependabot bumps);
- the template's Python matrix must be the range jm's ``pyproject.toml``
  classifiers declare, so a generated project tests what jm supports.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
TEMPLATE = ROOT / "src/just_makeit/templates/ci/github.yml"
WORKFLOWS = ROOT / ".github/workflows"
PYPROJECT = ROOT / "pyproject.toml"

_USES = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+)@(\S+)", re.M)


def _pins(text: str) -> "dict[str, set[str]]":
    """``{action: {ref, ...}}`` for every ``uses:`` line in *text*."""
    out: "dict[str, set[str]]" = {}
    for action, ref in _USES.findall(text):
        out.setdefault(action, set()).add(ref)
    return out


def _own_pins() -> "dict[str, set[str]]":
    merged: "dict[str, set[str]]" = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for action, refs in _pins(wf.read_text(encoding="utf-8")).items():
            merged.setdefault(action, set()).update(refs)
    return merged


def test_the_template_pins_something():
    """An empty result must not read as agreement."""
    assert _pins(TEMPLATE.read_text(encoding="utf-8")), (
        f"no `uses:` line found in {TEMPLATE.name} -- the pattern no "
        f"longer matches the template, so the check below checks nothing"
    )


def test_every_template_pin_matches_jms_own_workflows():
    own = _own_pins()
    stale = []
    for action, refs in _pins(TEMPLATE.read_text(encoding="utf-8")).items():
        for ref in refs:
            if action not in own:
                stale.append(f"{action}@{ref}: jm's own CI does not use it")
            elif ref not in own[action]:
                stale.append(
                    f"{action}@{ref}: jm's own CI pins "
                    f"{', '.join(sorted(own[action]))}"
                )
    assert not stale, (
        "the CI `jm ci` generates is pinned differently from jm's own "
        "workflows (Dependabot bumps those, never the template):\n  "
        + "\n  ".join(stale)
    )


def test_the_template_matrix_is_the_python_range_jm_supports():
    declared = re.findall(
        r'"Programming Language :: Python :: (3\.\d+)"',
        PYPROJECT.read_text(encoding="utf-8"),
    )
    assert declared, "no Python version classifiers in pyproject.toml"
    m = re.search(
        r"python-version:\s*\[([^\]]*)\]", TEMPLATE.read_text(encoding="utf-8")
    )
    assert m, f"no python-version matrix list in {TEMPLATE.name}"
    matrix = re.findall(r'"(3\.\d+)"', m.group(1))
    assert matrix == declared, (
        f"the generated CI tests {matrix}, but jm supports {declared} "
        f"(pyproject.toml classifiers)"
    )
