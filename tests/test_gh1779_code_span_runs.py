"""gh-1779: no Markdown code span may hold a run of whitespace.

A code span renders its content verbatim, so a span holding a run shows
the run on the docs site. mdformat makes one: a span hand-wrapped across an
indented line break gets the indent joined into it, and nothing afterwards
rewraps inside a span (gh-1630's mechanism). gh-1630 gated the changelog
fragments; on 2026-10-01 seven spans under ``docs/`` held a run, among them
`` `void make_window(float   *out, size_t n)` ``.

``scripts/check_code_spans.py`` widens the check to every tracked Markdown
file, reading spans with the vendored ``changelog.span_runs`` so there is one
reader, not two. These tests seed the shapes, then run it over the real tree.

GATE: no tracked Markdown code span holds a run of whitespace -- a
      deliberate run goes in a fenced block (gh-1779).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "check_code_spans.py"


def _run(*paths: Path, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(p) for p in paths]],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_span_wrapped_across_an_indent_is_caught(tmp_path: Path):
    """The raw form, before mdformat joins it: a newline plus indent."""
    f = tmp_path / "doc.md"
    f.write_text("- see `jm\n    upgrade` for the move\n", encoding="utf-8")
    r = _run(f)
    assert r.returncode == 1, r.stdout
    assert f"{f}:1: `jm\\n    upgrade`" in r.stdout, r.stdout


def test_a_joined_run_is_caught(tmp_path: Path):
    """The form mdformat leaves behind, on one line."""
    f = tmp_path / "doc.md"
    f.write_text("call `fn(h, out,   max_out)` here\n", encoding="utf-8")
    r = _run(f)
    assert r.returncode == 1, r.stdout
    assert "`fn(h, out,   max_out)`" in r.stdout, r.stdout


def test_a_run_in_a_fenced_block_is_allowed(tmp_path: Path):
    """Where a deliberate run goes -- why-zensical.md's definition list."""
    f = tmp_path / "doc.md"
    f.write_text(
        "Spacing between `a` and `b`  is prose, not a span.\n\n"
        "```markdown\nTerm\n:   Definition `x   y`\n```\n",
        encoding="utf-8",
    )
    r = _run(f)
    assert r.returncode == 0, r.stdout


def test_the_changelog_is_left_to_its_own_gates(tmp_path: Path):
    """CHANGELOG.md's released sections are history, and changelog.d/ is
    ``changelog-check``'s; the default file list must leave both out."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "changelog.d" / "fixed").mkdir(parents=True)
    (tmp_path / "changelog.d" / "fixed" / "x.md").write_text(
        "- `jm   upgrade`\n", encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "- `jm   upgrade`\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "page.md").write_text(
        "- `jm   upgrade`\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    r = _run(cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "docs/page.md:1:" in r.stdout, r.stdout
    assert "CHANGELOG.md" not in r.stdout, r.stdout
    assert "changelog.d/fixed" not in r.stdout, r.stdout


def test_the_repo_is_clean():
    """The gate over its real subject -- every tracked Markdown file.

    On the commit before the fix this fails naming the seven ``docs/`` spans
    gh-1779 lists, plus one in ``CLAUDE.md`` and one in the ring_buffer
    example's README.
    """
    r = _run()
    assert r.returncode == 0, r.stdout


def test_lint_runs_it():
    """Wired into the target CI runs, asked of make rather than grepped."""
    r = subprocess.run(
        ["make", "-nrR", "lint"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "check_code_spans.py" in r.stdout, r.stdout[-2000:]
