"""gh-1630: a changelog code span holding a whitespace run fails `make lint`.

A code span renders verbatim, so `` `jm   upgrade` `` ships as exactly that
in the release notes. The run is made by ``make format``: a span hand-wrapped
across a fragment's line break is joined by mdformat 1.0.0 with the item's
extra indent inside it (``jm`` / ``    upgrade`` -> ``jm   upgrade``).

GATE: `make lint` (code-span-check) fails on a code span holding a run of
    whitespace in a changelog.d/ fragment or in [Unreleased], both before
    the formatter joins a split span and after, naming the file and line.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_code_spans.py"

#: A fragment as an author wraps it: the span split over the line break.
SPLIT = "- **Thing** (gh-1). Run `jm\n    upgrade` and then\n    carry on.\n"
#: The same fragment after `make format`: exactly what mdformat 1.0.0 (the
#: pinned one) wrote for SPLIT, measured 2026-10-01.
JOINED = "- **Thing** (gh-1). Run `jm   upgrade` and then\n    carry on.\n"
CLEAN = "- **Thing** (gh-1). Run `jm upgrade` and then\n    carry on.\n"


def _load():
    spec = importlib.util.spec_from_file_location("_spans_1630", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path, fragment=None, changelog=None):
    if fragment is not None:
        d = tmp_path / "changelog.d" / "fixed"
        d.mkdir(parents=True)
        (d / "gh-1-thing.md").write_text(fragment, encoding="utf-8")
    if changelog is not None:
        (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


def _run(root):
    """The gate exactly as `make code-span-check` runs it."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("fragment", [SPLIT, JOINED], ids=["split", "joined"])
def test_a_spaced_span_in_a_fragment_fails(tmp_path, fragment):
    r = _run(_tree(tmp_path, fragment=fragment))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "changelog.d/fixed/gh-1-thing.md:1:" in r.stdout


def test_a_clean_fragment_passes(tmp_path):
    r = _run(_tree(tmp_path, fragment=CLEAN))
    assert r.returncode == 0, r.stdout + r.stderr


def test_unreleased_is_checked_and_released_is_not(tmp_path):
    """[Unreleased] has not shipped; a released section is history."""
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n" + JOINED + "\n"
        "## [1.0.0] - 2026-01-01\n\n### Fixed\n\n" + JOINED
    )
    r = _run(_tree(tmp_path, changelog=changelog))
    assert r.returncode == 1
    findings = r.stdout.strip().splitlines()
    assert findings == ["CHANGELOG.md [Unreleased]:7: `jm   upgrade`"]


def test_no_unreleased_section_checks_nothing(tmp_path):
    """Right after a release there is no [Unreleased]: nothing unshipped."""
    changelog = "# Changelog\n\n## [1.0.0] - 2026-01-01\n\n- x\n" + JOINED
    r = _run(_tree(tmp_path, changelog=changelog))
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_span_closes_only_on_a_run_of_its_own_length():
    """CommonMark pairing: a single tick inside a double-tick span is text."""
    assert [s for _, s in _load().spans("``x ` y`` z")] == ["x ` y"]


def test_deliberate_spacing_in_a_fenced_block_passes(tmp_path):
    """The remedy the refusal names must itself pass the gate."""
    fragment = (
        "- **Thing** (gh-1). It now prints:\n\n"
        "    ```\n    `jm   upgrade`    aligned\n    ```\n"
    )
    r = _run(_tree(tmp_path, fragment=fragment))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`a`  and  `b`", []),  # the run is BETWEEN spans, not in one
        ("``x ` y`` z", []),  # a double-tick span may hold a single tick
        ("` padded `", []),  # one space of padding each side is legal
        ("`a  b`", [(1, "a  b")]),
        ("x\n`a\n    b`", [(2, "a\n    b")]),
        ("\\`a  b `c`", []),  # an escaped tick opens nothing
    ],
)
def test_span_pairing(text, expected):
    assert _load().findings(text) == expected


def test_this_repo_is_clean():
    """What `make lint` sees on this tree."""
    r = _run(ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
