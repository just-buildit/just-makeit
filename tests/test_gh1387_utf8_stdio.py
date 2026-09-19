"""gh-1387: jm's output survives a stdout that is not UTF-8.

On Windows a pipe or a redirect is encoded in the ANSI code page (cp1252),
which has no ``→``; ``jm upgrade`` printed one and died with
``UnicodeEncodeError`` between migration steps. PR CI could not see it:
pytest's capture is UTF-8, so every in-process test passed.

These run jm in a CHILD process with ``PYTHONIOENCODING=cp1252`` and its
stdout on a pipe -- the Windows condition, reproduced on any OS -- and
require the non-cp1252 text to arrive as UTF-8. The fix is per stream, not
per message, so the tests exercise the two ways jm output reaches a pipe:
``main()`` itself, and the ``test.py`` child ``jm example`` launches.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ARROW = "→"  # not in cp1252 -- the character that crashed upgrade

# The child imports jm from this checkout, as test_cli's do: CI runs the
# suite from source, not an install, and pytest's path is not inherited.
SRC = Path(__file__).parent.parent / "src"


def _run_cp1252(code: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run *code* in a child whose stdio default is cp1252, stdout piped."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=cwd,
        env={
            **os.environ,
            "PYTHONPATH": str(SRC),
            "PYTHONIOENCODING": "cp1252",
            "NO_COLOR": "1",
        },
        capture_output=True,
        timeout=300,
    )


def test_upgrade_prints_its_arrow_through_a_cp1252_pipe(tmp_path):
    # The reported instance: a schema migration line through main().
    r = _run_cp1252(
        """
        import os, re, sys
        from pathlib import Path
        from just_makeit._cli import main

        sys.argv = ["jm", "new", "proj"]
        main()
        os.chdir("proj")
        toml = Path("just-makeit.toml")
        text = toml.read_text(encoding="utf-8")
        toml.write_text(
            re.sub(r'(?m)^schema = .*$', 'schema = "6"', text),
            encoding="utf-8",
        )
        sys.argv = ["jm", "upgrade"]
        main()
        """,
        tmp_path,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert f"migrating schema 6 {ARROW} 7".encode() in r.stdout


def test_example_child_writes_utf8_through_a_cp1252_pipe(tmp_path):
    # `jm example` runs test.py as a separate interpreter on the SAME pipe,
    # which does not go through main(); it must be told the encoding too.
    ex = tmp_path / "ex"
    ex.mkdir()
    # The examples also capture jm with `text=True`, a decode in the LOCALE
    # encoding -- cp1252 on Windows -- that only UTF-8 mode changes. This
    # box's locale is UTF-8 whatever the fix does, so the decode itself
    # cannot fail here; the mode is asserted instead, in the child and in a
    # grandchild, which must inherit it.
    (ex / "test.py").write_text(
        "import subprocess, sys\n"
        "assert sys.flags.utf8_mode == 1, 'child not in UTF-8 mode'\n"
        "out = subprocess.run(\n"
        '    [sys.executable, "-c", "import sys; print(sys.flags.utf8_mode)"],\n'
        "    capture_output=True, text=True, check=True,\n"
        ").stdout\n"
        "assert out.strip() == '1', 'grandchild not in UTF-8 mode'\n"
        f"print({ARROW!r})\n",
        encoding="utf-8",
    )
    r = _run_cp1252(
        f"""
        import sys
        from pathlib import Path
        from just_makeit import _example
        from just_makeit._cli import main

        _example._find = lambda name: Path({str(ex)!r})
        sys.argv = ["jm", "example", "ex"]
        main()
        """,
        tmp_path,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert ARROW.encode() in r.stdout
