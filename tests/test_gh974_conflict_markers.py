"""gh-974: a merge-conflict marker must not survive `make lint`.

`docs/configuration.md` carried one from 2026-08-04 to 2026-08-14 — through
every lint run, several releases, and onto the **published docs site**, where
`\\<<\\<<\\<<< HEAD` and a seven-deep blockquote of a commit subject rendered
as page content.

The reason it survived is the reason this file tests the patterns rather than
the outcome: **mdformat normalises the markers instead of refusing them**, so
each pass made the corruption *less* visible.

    <<<<<<< HEAD          ->  \\<<\\<<\\<<< HEAD      every `<` escaped
    =======               ->  a setext H1 — the line above it is promoted to a
                              heading and the marker disappears entirely
    >>>>>>> d19e3ae (..)  ->  > > > > > > > d19e3ae   seven blockquotes

A check written against the literal three markers would therefore have found
**one** of the three in that file. The `=======` case is why the gate has to
run over tracked files on the way in: after formatting there is nothing left of
it to find.

The gate lives in `scripts/conflict-check.sh` rather than inside the make
recipe precisely so this can run it over seeded files. A lint target whose only
exercise is corrupting the repo is a target nobody proves — and the one thing
worth knowing about a gate is that it fails.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "conflict-check.sh"

# Every form the marker takes, raw and as mdformat rewrites it. The escaped
# ones are the file's real content as it was found, not a reconstruction.
MARKERS = {
    "raw-begin": "<<<<<<< HEAD\n",
    "raw-middle": "=======\n",
    "raw-end": ">>>>>>> d19e3ae (feat: something)\n",
    "mdformat-begin": "\\<<\\<<\\<<< HEAD\n",
    "mdformat-end": "> > > > > > > d19e3ae (feat: something)\n",
}


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(SCRIPT), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("name", sorted(MARKERS))
def test_each_marker_form_is_caught(tmp_path: Path, name: str):
    f = tmp_path / "doc.md"
    f.write_text(
        f"Some prose.\n\n{MARKERS[name]}\nMore prose.\n", encoding="utf-8"
    )
    r = _run(f)
    assert r.returncode == 1, r.stdout
    assert "conflict marker" in r.stdout


def test_clean_content_passes(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text(
        "Ordinary prose with `>>>` doctest arrows and a `<` or two.\n"
        "A shell heredoc line: cat <<EOF\n"
        "And an equals rule that is not seven long: ====\n",
        encoding="utf-8",
    )
    r = _run(f)
    assert r.returncode == 0, r.stdout


def test_a_marker_quoted_inside_a_code_block_is_allowed(tmp_path: Path):
    """The real case in `CHANGELOG.md`, which must keep passing.

    gh-785's entry quotes `jm apply` refusing a `.pyi` whose hand-written
    members sit around a conflict marker. That is documentation *about*
    conflicts, indented inside a fenced block — and git never writes a marker
    indented, which is what makes column 1 a sound discriminator.
    """
    f = tmp_path / "CHANGELOG.md"
    f.write_text(
        "```\n"
        "WARNING: 2 hand-written .pyi member(s) will not survive this render.\n"
        "  src/sp/thing.pyi:64: invalid syntax\n"
        "    <<<<<<< HEAD\n"
        "```\n",
        encoding="utf-8",
    )
    r = _run(f)
    assert r.returncode == 0, r.stdout


def test_the_repo_is_clean(tmp_path: Path):
    """The gate over its real subject — every tracked file.

    Sabotage, and the measurement this was built from: on the commit before
    the fix this fails, naming `docs/configuration.md` lines 435 and 509.
    """
    r = subprocess.run(
        ["sh", str(SCRIPT)], cwd=REPO, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


def test_lint_runs_it():
    """Wired into the gate CI actually runs, not merely present.

    `make lint` is the only thing ci.yml calls, so a target it does not depend
    on is a target that never runs. Asked of make itself rather than by
    grepping the makefile, so the answer accounts for how the prerequisite is
    declared.
    """
    r = subprocess.run(
        ["make", "-nrR", "lint"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "conflict-check.sh" in r.stdout, r.stdout[-2000:]
