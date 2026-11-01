"""gh-1569: `jm adopt`'s usage names every form its parser accepts.

`jm adopt --check --all` worked, and the usage line read
``adopt --check [--module <id>]`` -- so a reader learned the read-only form
covered one module when it covers everything. The parser now reads its
accepted options OUT of the usage text, so an option cannot be accepted
without being advertised; these tests hold the rest: `--help` exits 0, the
top-level help names every option the parser does, and an object name given
to `--check` (which surveys per module) is refused instead of ignored.

gh-2434: `--help` is answered by `_cli._main` for every command, from the
command's own entries of the top-level usage -- so `adopt --help` prints
those, and the parser's options are read from its source here.

GATE: `jm adopt` accepts only the options its usage text names, `--help`
      prints the usage and exits 0, the top-level help names each option
      the parser accepts, and `adopt --check` refuses an object name it
      would ignore.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh2057_verb_leaves_what_apply_writes import parser_flags


@pytest.fixture(scope="module")
def proj(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh1569")
    r = run_cli("new", "p", "--object", "g", cwd=base)
    assert r.returncode == 0, r.stdout + r.stderr
    return base / "p"


def _usage() -> str:
    r = run_cli("adopt", "--help")
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_help_prints_the_usage_and_exits_0():
    out = _usage()
    assert out.startswith("Usage: just-makeit\n  adopt --check"), out
    assert "adopt --check [--module ID | --all]" in out, out
    assert "adopt --packaging" in out, out


def test_the_top_level_help_names_every_adopt_option():
    top = run_cli("--help").stdout
    lines = [ln.strip() for ln in top.splitlines()]
    lines = [ln for ln in lines if ln.startswith("adopt")]
    assert lines, top
    assert (
        lines[0].split("  ")[0].strip()
        == "adopt --check [--module ID | --all]"
    )
    advertised = set(re.findall(r"--[a-z][a-z-]*", "\n".join(lines)))
    accepted = parser_flags("adopt")
    assert {"--check", "--all", "--packaging"} <= accepted, accepted
    assert accepted <= advertised, accepted - advertised


def test_check_all_is_accepted(proj):
    r = run_cli("adopt", "--check", "--all", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_option_the_usage_does_not_name_is_refused(proj):
    r = run_cli("adopt", "--check", "--everything", cwd=proj)
    assert r.returncode == 2
    assert "unknown option --everything" in r.stderr


def test_check_refuses_an_object_name_it_would_ignore(proj):
    r = run_cli("adopt", "--check", "g", cwd=proj)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not object names (g)" in r.stderr
