"""gh-2654: every text-mode file open names its encoding.

#2651's test wrote a rendered ``jm_perf.h`` with ``Path.write_text(text)``
and no ``encoding=``. The header holds an em dash; Python on Windows
defaults to cp1252, so the one Windows leg that ran it failed with
``UnicodeEncodeError: 'charmap' codec can't encode characters``, while
Linux and macOS -- UTF-8 locales -- passed. On 2026-11-16 there were
hundreds more such calls in tests/, passing only because their text was
ASCII: a property of the content, not of the call.

``scripts/check_text_encoding.py`` reads the call shape by name -- ruff's
``PLW1514`` needs an inferable ``Path`` receiver and saw 9 of ~1360 (the
script's docstring has the measurement). ``make lint`` runs it through
``text-encoding-check``. These tests seed the shapes, then run that target
over the real tree.

GATE: every read_text, write_text and text-mode open names its encoding=
      -- the platform default is cp1252 on Windows (gh-2654).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "check_text_encoding.py"


def _run(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_each_unspecified_shape_is_caught(tmp_path: Path):
    """The shapes that use the platform codec, one per line."""
    f = tmp_path / "m.py"
    f.write_text(
        "p.read_text()\n"
        "p.write_text(s)\n"
        "open(p)\n"
        "open(p, 'w')\n"
        "io.open(p, mode='a')\n"
        "p.open('a')\n"
        "tempfile.NamedTemporaryFile('w')\n"
        "open(p, mode)\n",
        encoding="utf-8",
    )
    r = _run(str(f))
    assert r.returncode == 1, r.stdout
    for line, call in enumerate(
        [
            ".read_text()",
            ".write_text()",
            "open()",
            "open('w')",
            "open('a')",
            ".open('a')",
            "NamedTemporaryFile('w')",
            "open(mode)",
        ],
        start=1,
    ):
        assert f"{f}:{line}: {call}\n" in r.stdout, r.stdout
    assert "8 call(s)" in r.stdout, r.stdout


def test_explicit_binary_and_non_file_opens_pass(tmp_path: Path):
    """What must NOT be refused: an encoding given either way, a binary
    mode, jm's own UTF-8 writer, and ``open`` on a module that is not a
    text-file opener."""
    f = tmp_path / "ok.py"
    f.write_text(
        "p.read_text(encoding='utf-8')\n"
        "p.read_text('utf-8')\n"
        "p.write_text(s, encoding='utf-8')\n"
        "p.write_text(s, 'utf-8')\n"
        "p.write_text(s, **kw)\n"
        "_textio.write_text(p, s)\n"
        "open(p, 'rb')\n"
        "open(p, 'w', -1, 'utf-8')\n"
        "p.open('wb')\n"
        "p.open(encoding='utf-8')\n"
        "tarfile.open(fileobj=b)\n"
        "os.open(p, os.O_RDONLY)\n"
        "tempfile.NamedTemporaryFile()\n",
        encoding="utf-8",
    )
    r = _run(str(f))
    assert r.returncode == 0, r.stdout


def test_ruff_excludes_and_given_excludes_are_skipped(tmp_path: Path):
    """The walk honours ruff's ``extend-exclude`` (templates are Jinja, not
    Python) and ``--exclude`` (the vendored files), over tracked files."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nextend-exclude = ["tpl/"]\n', encoding="utf-8"
    )
    for rel in ("tpl/t.py", "vendored.py", "mine.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("p.read_text()\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    r = _run("--exclude", "vendored.py", cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "mine.py:1: .read_text()" in r.stdout, r.stdout
    assert "tpl/" not in r.stdout, r.stdout
    assert "vendored.py" not in r.stdout, r.stdout


def test_the_repo_is_clean():
    """The gate over its real subject, run as ``make lint`` runs it.

    On the commit before the fix this names 1360 calls, all but seven of
    them a bare ``read_text()`` or ``write_text(s)``.
    """
    r = subprocess.run(
        ["make", "-s", "text-encoding-check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_lint_runs_it():
    """Wired into the target CI runs, asked of make rather than grepped."""
    r = subprocess.run(
        ["make", "-nrR", "lint"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert "check_text_encoding.py" in r.stdout, r.stdout[-2000:]
