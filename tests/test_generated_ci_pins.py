"""The CI jm generates for a project is pinned the way jm's own CI is.

GATE: every action the generated CI pins matches jm's own workflows, and it tests the project's own Python list.

``templates/ci/github.yml`` is the workflow ``jm ci`` writes into every
scaffolded project. It pinned ``actions/checkout@v4`` and
``actions/setup-python@v5`` -- both ``node20``, which GitHub now forces onto
Node 24 with a deprecation notice on every run -- and its matrix stopped at
3.13 while jm itself supported 3.14. jm's own pins had moved on long before.

Dependabot cannot see this file: it scans ``.github/workflows/`` and nothing
else, so a template is exactly where a pin goes stale unwatched. Rather than
keep a second list current, the checks here DERIVE the answer:

- each action the template uses must appear in jm's own workflows at the
  same ref, so the template follows jm's pins (which Dependabot bumps);
- the Python matrix is no list at all in the template (gh-2629): it is the
  PROJECT's ``pyproject.toml`` classifiers, rendered by ``jm ci`` and kept
  equal to them by every ``jm apply``. A new project's classifiers are jm's
  own, from jm's installed metadata; after that the project owns its list.
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from _jmrun import run_cli
from just_makeit import _config as C

ROOT = Path(__file__).parent.parent
TEMPLATE = ROOT / "src/just_makeit/templates/ci/github.yml"
TEMPLATES = ROOT / "src/just_makeit/templates"
WORKFLOWS = ROOT / ".github/workflows"
CI = ".github/workflows/ci.yml"

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


# -- gh-2629: the Python list is the project's pyproject.toml ------------

_VERSION = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
_LITERAL = re.compile(r"[\"'](3\.\d+)[\"']")
_NOTE = "declares no `Programming Language"


def _jm_versions() -> "list[str]":
    """jm's own list, read from its installed metadata -- not through
    `_pythons`, so a fault in the module under test is not also in the
    oracle."""
    found = [
        m.group(1)
        for c in metadata.metadata("just-makeit").get_all("Classifier")
        if (m := _VERSION.match(c))
    ]
    assert found, "jm's own metadata declares no 3.N classifier"
    return found


def _declared(root: Path) -> "list[str]":
    with (root / "pyproject.toml").open("rb") as f:
        doc = C.tomllib.load(f)
    return [
        m.group(1)
        for c in doc["project"].get("classifiers", [])
        if (m := _VERSION.match(c))
    ]


def _ci_list(root: Path) -> "list[str]":
    lists = re.findall(
        r"^\s*python-version:\s*\[([^\]]*)\]",
        (root / CI).read_text(encoding="utf-8"),
        re.M,
    )
    assert len(lists) == 1, f"expected one python-version list, got {lists}"
    return re.findall(r'"(3\.\d+)"', lists[0])


def _new(tmp_path: Path, *, ci: bool = True) -> Path:
    assert run_cli("new", "pv", cwd=tmp_path).returncode == 0
    root = tmp_path / "pv"
    if ci:
        r = run_cli("ci", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    return root


def _drop_classifier(root: Path, version: str) -> None:
    p = root / "pyproject.toml"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    keep = [ln for ln in lines if f'Python :: {version}"' not in ln]
    assert len(keep) == len(lines) - 1, f"no classifier for {version}"
    p.write_text("".join(keep), encoding="utf-8")


def test_no_template_carries_a_python_version_literal():
    """The CI and pyproject templates name no Python version: both are
    slots, so there is no list in jm's source to fall behind."""
    found = {
        rel: _LITERAL.findall((TEMPLATES / rel).read_text(encoding="utf-8"))
        for rel in ("ci/github.yml", "toml/pyproject.toml")
    }
    assert not any(found.values()), f"hand-kept versions: {found}"
    assert "<<python_versions>>" in TEMPLATE.read_text(encoding="utf-8")


def test_a_new_project_declares_the_pythons_jm_supports(tmp_path):
    root = _new(tmp_path, ci=False)
    assert _declared(root) == _jm_versions()
    with (root / "pyproject.toml").open("rb") as f:
        req = C.tomllib.load(f)["project"]["requires-python"]
    assert req == metadata.metadata("just-makeit")["Requires-Python"]


def test_the_ci_tests_the_projects_classifiers(tmp_path):
    root = _new(tmp_path)
    assert _ci_list(root) == _declared(root) == _jm_versions()
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_dropping_a_classifier_drops_its_leg_on_apply(tmp_path):
    """The author's list moves; `status` calls the CI STALE until `apply`
    rewrites it, and then nothing is left to report."""
    root = _new(tmp_path)
    oldest = _declared(root)[0]
    _drop_classifier(root, oldest)

    st = run_cli("status", "--check", cwd=root)
    assert st.returncode != 0, st.stdout
    assert CI in st.stdout, st.stdout
    assert oldest in _ci_list(root)

    ap = run_cli("apply", cwd=root)
    assert ap.returncode == 0, ap.stdout + ap.stderr
    assert f"update  {CI}" in ap.stdout, ap.stdout
    assert _ci_list(root) == _declared(root)
    assert oldest not in _ci_list(root)
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_apply_rewrites_only_the_list(tmp_path):
    """The rest of the workflow is the author's to tune: only the captured
    list moves."""
    root = _new(tmp_path)
    ci = root / CI
    ci.write_text(
        ci.read_text(encoding="utf-8") + "# the author's own line\n",
        encoding="utf-8",
    )
    before = ci.read_text(encoding="utf-8")
    oldest = _declared(root)[0]
    _drop_classifier(root, oldest)
    assert run_cli("apply", cwd=root).returncode == 0
    assert ci.read_text(encoding="utf-8") == before.replace(
        f'"{oldest}", ', "", 1
    )


def test_a_project_without_classifiers_keeps_its_pyproject(tmp_path):
    """No classifiers (a project older than gh-2629): pyproject.toml is the
    author's and is not written; the CI follows jm's list, and says how to
    choose another."""
    root = _new(tmp_path)
    p = root / "pyproject.toml"
    text = p.read_text(encoding="utf-8")
    stripped = re.sub(r"classifiers = \[[^\]]*\]\n", "", text)
    assert stripped != text
    p.write_text(stripped, encoding="utf-8")
    # A CI on some other list, so the fallback has something to rewrite.
    ci = root / CI
    ci.write_text(
        re.sub(
            r"python-version: \[[^\]]*\]",
            'python-version: ["3.10"]',
            ci.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )

    ap = run_cli("apply", cwd=root)
    assert ap.returncode == 0, ap.stdout + ap.stderr
    assert _NOTE in ap.stdout, ap.stdout
    assert p.read_text(encoding="utf-8") == stripped
    assert _ci_list(root) == _jm_versions()

    r = run_cli("ci", "--force", cwd=root)
    assert _NOTE in r.stdout, r.stdout
    assert _ci_list(root) == _jm_versions()


def test_a_workflow_jm_does_not_understand_is_left_alone(tmp_path):
    """Two `python-version` lists is not a file whose list jm can name."""
    root = _new(tmp_path)
    ci = root / CI
    text = ci.read_text(encoding="utf-8")
    m = re.search(r"^\s*python-version: \[[^\]]*\]\n", text, re.M)
    assert m
    doubled = text[: m.end()] + m.group(0) + text[m.end() :]
    ci.write_text(doubled, encoding="utf-8")
    _drop_classifier(root, _declared(root)[0])
    assert run_cli("apply", cwd=root).returncode == 0
    assert ci.read_text(encoding="utf-8") == doubled
