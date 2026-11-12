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

import pytest

from _jmrun import run_cli
from _pyfloor import python_floor
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
#: Any ``3.<minor>`` token -- quoted, after an operator, in prose (``3.9+``),
#: in an image tag (``python:3.12-bookworm``), with a patch or a suffix. Not
#: one preceded by a digit or a dot, which is part of some other number.
_LITERAL = re.compile(r"(?<![\d.])3\.\d+[\w.*+-]*")

#: The template ``3.N`` tokens that are not a Python version jm keeps, per
#: file, each with why. A stale entry fails too.
_NOT_A_PYTHON = {
    "cmake/CMakeLists_top.cmake": {
        "3.16": "CMake's own minimum (`cmake_minimum_required`)",
        "3.17": "a CMake version a comment names",
        "3.17+": "a CMake version a comment names",
    },
}
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


def test_the_literal_pattern_sees_every_shape():
    """Planted: each way a template has spelled a Python version is seen,
    and a number that merely contains ``3.N`` is not."""
    for text, token in (
        ('python-version: ["3.9", "3.10"]', "3.9"),
        ('requires-python = ">=3.9"', "3.9"),
        ("- Python 3.9+", "3.9+"),
        ("image: python:3.12-bookworm", "3.12-bookworm"),
        ("python3.11 -m venv", "3.11"),
        ("3.10.4", "3.10.4"),
        ("3.13t", "3.13t"),
        ("3.14.0rc1", "3.14.0rc1"),
    ):
        assert _LITERAL.findall(text)[:1] == [token], text
    for text in ("13.9", "0.3.9", "v1.3.12"):
        assert not _LITERAL.findall(text), text


def test_no_template_carries_a_python_version_literal():
    """No template names a Python version: each is a slot, read from the
    project's pyproject.toml, so there is no list in jm's source to fall
    behind -- the CI matrix, the generated ``pyproject.toml`` and every
    PEP 723 app's floor (gh-2629, gh-2631). Every other ``3.N`` token is
    named in `_NOT_A_PYTHON`."""
    found: "dict[str, set[str]]" = {}
    for path in sorted(TEMPLATES.rglob("*")):
        if path.is_file():
            toks = _LITERAL.findall(path.read_text(encoding="utf-8"))
            if toks:
                found[path.relative_to(TEMPLATES).as_posix()] = set(toks)
    unexplained = {
        rel: sorted(toks - set(_NOT_A_PYTHON.get(rel, {})))
        for rel, toks in found.items()
        if toks - set(_NOT_A_PYTHON.get(rel, {}))
    }
    assert not unexplained, f"hand-kept Python versions: {unexplained}"
    stale = {
        rel: sorted(set(toks) - found.get(rel, set()))
        for rel, toks in _NOT_A_PYTHON.items()
        if set(toks) - found.get(rel, set())
    }
    assert not stale, f"_NOT_A_PYTHON names tokens no longer there: {stale}"
    assert "<<python_versions>>" in TEMPLATE.read_text(encoding="utf-8")


def test_a_new_project_declares_the_pythons_jm_supports(tmp_path):
    root = _new(tmp_path, ci=False)
    assert _declared(root) == _jm_versions()
    assert python_floor(root / "pyproject.toml") == python_floor()


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
    ap = run_cli("apply", cwd=root)
    assert ap.returncode == 0, ap.stdout + ap.stderr
    assert ci.read_text(encoding="utf-8") == doubled
    # ...and is not silent: apply says so, and status reports it.
    assert "it has 2 `python-version` lists" in ap.stdout, ap.stdout
    st = run_cli("status", cwd=root)
    assert "CI PYTHONS (1)" in st.stdout, st.stdout
    assert "it has 2 `python-version` lists" in st.stdout, st.stdout


def test_a_workflow_with_no_list_is_reported(tmp_path):
    """A matrix computed some other way (`fromJSON`) has no list to keep:
    named, never written, and not counted."""
    root = _new(tmp_path)
    ci = root / CI
    text = re.sub(
        r"python-version: \[[^\]]*\]",
        "python-version: ${{ fromJSON(needs.p.outputs.pythons) }}",
        ci.read_text(encoding="utf-8"),
    )
    ci.write_text(text, encoding="utf-8")
    ap = run_cli("apply", cwd=root)
    assert "it has no `python-version` list" in ap.stdout, ap.stdout
    assert ci.read_text(encoding="utf-8") == text
    st = run_cli("status", "--check", cwd=root)
    assert st.returncode == 0, st.stdout
    assert "CI PYTHONS (1)" in st.stdout, st.stdout


def _respell(root: Path, shape: str) -> str:
    """Rewrite the CI's one-line list as *shape*, and return the file."""
    ci = root / CI
    text = ci.read_text(encoding="utf-8")
    m = re.search(r"^( *)python-version: \[([^\]]*)\]\n", text, re.M)
    assert m, text
    ind, items = m.group(1), [i.strip() for i in m.group(2).split(",")]
    if shape == "block":
        body = "".join(f"{ind}  - {i}\n" for i in items)
        new = f"{ind}python-version:  # ours\n{body}"
    else:
        body = "".join(f"{ind}  {i},\n" for i in items)
        new = f"{ind}python-version: [\n{body}{ind}]\n"
    text = text[: m.start()] + new + text[m.end() :]
    ci.write_text(text, encoding="utf-8")
    return text


@pytest.mark.parametrize("shape", ["block", "flow-multiline"])
def test_a_reformatted_list_is_still_kept(tmp_path, shape):
    """A list the author re-spelled is still the one list: `status` calls it
    STALE, and `apply` rewrites the versions in the author's own shape."""
    root = _new(tmp_path)
    before = _respell(root, shape)
    assert run_cli("status", "--check", cwd=root).returncode == 0
    oldest = _declared(root)[0]
    _drop_classifier(root, oldest)

    st = run_cli("status", "--check", cwd=root)
    assert st.returncode != 0, st.stdout
    assert CI in st.stdout, st.stdout

    assert run_cli("apply", cwd=root).returncode == 0
    after = (root / CI).read_text(encoding="utf-8")
    gone = [ln for ln in before.splitlines() if f'"{oldest}"' in ln]
    assert len(gone) == 1, gone
    assert after.splitlines() == [
        ln for ln in before.splitlines() if ln != gone[0]
    ]
    assert run_cli("status", "--check", cwd=root).returncode == 0


def _set_requires_python(root: Path, spec: str) -> None:
    p = root / "pyproject.toml"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(
        r'^requires-python = "[^"]*"$',
        f'requires-python = "{spec}"',
        text,
        flags=re.M,
    )
    assert n == 1, text
    p.write_text(new, encoding="utf-8")


def test_a_classifier_requires_python_refuses_gets_no_leg(tmp_path):
    """pip will not install the project on a Python its requires-python
    refuses, so that classifier gets no leg -- and the author is told."""
    root = _new(tmp_path)
    vs = _declared(root)
    assert len(vs) >= 3, vs
    cap = vs[-2]  # refuse the two newest
    _set_requires_python(root, f">={vs[0]},<{cap}")
    ap = run_cli("apply", cwd=root)
    assert ap.returncode == 0, ap.stdout + ap.stderr
    assert _ci_list(root) == vs[:-2]
    assert f"classifies Python {vs[-2]}, {vs[-1]}" in ap.stdout, ap.stdout
    r = run_cli("ci", "--force", cwd=root)
    assert _ci_list(root) == vs[:-2]
    assert "requires-python refuses them" in r.stdout, r.stdout


def test_the_readme_names_the_projects_floor(tmp_path):
    """The README's `Python 3.N+` is the floor pyproject.toml declares."""
    root = _new(tmp_path, ci=False)
    floor = ".".join(map(str, python_floor(root / "pyproject.toml")))
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"- Python {floor}+\n" in readme, readme


def test_the_woodpecker_image_is_the_oldest_leg(tmp_path):
    root = _new(tmp_path, ci=False)
    assert run_cli("ci", "--provider", "woodpecker", cwd=root).returncode == 0
    wp = root / ".woodpecker.yml"

    def image() -> "list[str]":
        return re.findall(
            r"image: python:(3\.\d+)-", wp.read_text(encoding="utf-8")
        )

    assert image() == [_declared(root)[0]]
    _drop_classifier(root, _declared(root)[0])
    assert run_cli("status", "--check", cwd=root).returncode != 0
    assert run_cli("apply", cwd=root).returncode == 0
    assert image() == [_declared(root)[0]]


def test_jm_new_reads_jms_metadata_once_per_process(tmp_path, monkeypatch):
    """jm's installed metadata is read once and cached: on 3.9 an uncached
    read walks the whole `sys.path`, and four per `jm new` timed every 3.9
    CI leg out (#2633). Two `jm new` runs, at most one read."""
    from just_makeit import _pythons

    calls: "list[str]" = []
    real = metadata.metadata

    def counting(name):
        calls.append(name)
        return real(name)

    _pythons._jm_metadata.cache_clear()
    monkeypatch.setattr(metadata, "metadata", counting)
    for name in ("pa", "pb"):
        assert run_cli("new", name, cwd=tmp_path).returncode == 0
    assert calls.count("just-makeit") == 1, calls


# -- gh-2631: a PEP 723 app script declares the project's floor -----------


def _app_floor(root: Path) -> str:
    m = re.search(
        r'^# requires-python = "([^"]*)"$',
        (root / "tool.py").read_text(encoding="utf-8"),
        re.M,
    )
    assert m, "the app script declares no floor"
    return m.group(1)


def _pep723_project(tmp_path: Path) -> Path:
    root = _new(tmp_path, ci=False)
    assert run_cli("object", "eng", cwd=root).returncode == 0
    r = run_cli(
        "app",
        "--target",
        "pep723",
        "--object",
        "eng",
        "--name",
        "tool",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def test_a_pep723_app_declares_the_projects_floor(tmp_path):
    """Raise the project's floor: `status` calls the script STALE, `apply`
    rewrites its floor to match, and nothing is left to report."""
    root = _pep723_project(tmp_path)
    jm_floor = metadata.metadata("just-makeit")["Requires-Python"]
    assert _app_floor(root) == jm_floor

    p = root / "pyproject.toml"
    text = p.read_text(encoding="utf-8")
    raised = text.replace(f'"{jm_floor}"', '">=3.12"', 1)
    assert raised != text
    p.write_text(raised, encoding="utf-8")

    st = run_cli("status", "--check", cwd=root)
    assert st.returncode != 0 and "tool.py" in st.stdout, st.stdout
    assert run_cli("apply", cwd=root).returncode == 0
    assert _app_floor(root) == ">=3.12"
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_a_pep723_app_without_a_project_floor_takes_jms(tmp_path):
    """No floor in pyproject.toml: the script declares jm's, and the
    author's file is not written."""
    root = _pep723_project(tmp_path)
    p = root / "pyproject.toml"
    jm_floor = metadata.metadata("just-makeit")["Requires-Python"]
    # Off jm's floor first, so falling back is a change apply must make.
    p.write_text(
        p.read_text(encoding="utf-8").replace(f'"{jm_floor}"', '">=3.12"', 1),
        encoding="utf-8",
    )
    assert run_cli("apply", cwd=root).returncode == 0
    assert _app_floor(root) == ">=3.12"
    text = p.read_text(encoding="utf-8")
    stripped = re.sub(r"^requires-python = .*\n", "", text, flags=re.M)
    assert stripped != text
    p.write_text(stripped, encoding="utf-8")
    assert run_cli("apply", cwd=root).returncode == 0
    assert _app_floor(root) == jm_floor
    assert p.read_text(encoding="utf-8") == stripped
