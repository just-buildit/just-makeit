"""gh-1647: `jm upgrade`'s complex respell reaches the manifest it renders from.

gh-1248's respell rewrites the pre-gh-1246 ``float complex`` spelling in the
project's C. But an object's step and lifecycle bodies live in the MANIFEST
(``impl``, ``create_impl``, ...), and jm renders the header body FROM them.
Respelling only the header left ``jm apply`` to put ``float complex`` back,
printing "the manifest is the source of truth -- overwriting the header from
it", and every later upgrade re-reported the same file: the two never
converged, and the spelling gh-1246 exists to remove stayed in the installed
header (doppler's ``wfm_synth``, jm 0.91.0).

The manifest's C goes through the one walker the ``c_prefix`` respell uses
(``_csym.respell_manifest_c``, gh-1653); that every C respell in `_upgrade`
does is tests/test_gh1647_one_manifest_walker.py.

GATE: after `jm upgrade` then `jm apply`, the manifest body and the rendered
      header both say ``_Complex``, `apply` does not overwrite the header, a
      second upgrade changes nothing, prose inside the body keeps its words,
      and the header parses as C++.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from _jmrun import run_cli
from just_makeit import _incpath
from just_makeit import _textio

BODIES = '''impl = """
    float complex sym = x; /* float complex, quoted in a comment */
    const char *why = "float complex, quoted in a string";
    (void)why;
    (void)state;
    return sym;
"""
create_impl = """
    double complex seed = 0; /* double complex, quoted */
    (void)seed;
"""
'''

OVERWRITE = "overwriting the header from it"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """A project whose step body arrived in the pre-gh-1246 spelling, and
    the output of `apply`, `upgrade`, `apply`, `upgrade` over it. Commands
    only: every assertion is in a test, so a failure is a FAILED test."""
    where = tmp_path_factory.mktemp("g1647")
    run_cli("new", "p", "--no-c-prefix", "--object", "g", cwd=where)
    root = where / "p"
    frag = root / "objects" / "g.toml"
    text = frag.read_text(encoding="utf-8")
    at = text.index('no_step = "false"\n') + len('no_step = "false"\n')
    _textio.write_text(frag, text[:at] + BODIES + text[at:])
    before = run_cli("apply", cwd=root)
    header = _incpath.header_root(root) / "g" / "g_core.h"
    old_header = header.read_text(encoding="utf-8")
    first = run_cli("upgrade", cwd=root)
    after = run_cli("apply", cwd=root)
    # The header a user BUILDS: after upgrade then apply. Read here, before
    # the second upgrade -- which would respell it again and hide a revert.
    built = header.read_text(encoding="utf-8")
    second = run_cli("upgrade", cwd=root)
    return root, built, old_header, before, first, after, second


def test_the_fixture_starts_in_the_old_spelling(tree):
    """Not vacuous: the header really carried the bare spelling first."""
    _, _, old_header, before, *_ = tree
    assert before.returncode == 0, before.stdout + before.stderr
    assert "float complex sym = x;" in old_header, old_header


def test_the_manifest_and_the_header_both_say_complex(tree):
    root, h, *_ = tree
    frag = (root / "objects" / "g.toml").read_text(encoding="utf-8")
    assert "float _Complex sym = x;" in frag, frag
    assert "double _Complex seed = 0;" in frag, frag
    assert "float _Complex sym = x;" in h, h
    assert "float complex sym" not in h, h


def test_apply_does_not_overwrite_the_upgraded_header(tree):
    *_, after, _ = tree
    out = after.stdout + after.stderr
    assert after.returncode == 0, out
    assert OVERWRITE not in out, out


def test_a_second_upgrade_changes_nothing(tree):
    *_, first, _, second = tree
    assert "respelled the complex types" in first.stdout, first.stdout
    assert "respelled the complex types" not in second.stdout, second.stdout


def test_prose_inside_a_body_keeps_its_words(tree):
    root, h, *_ = tree
    frag = (root / "objects" / "g.toml").read_text(encoding="utf-8")
    for prose in (
        "/* float complex, quoted in a comment */",
        '"float complex, quoted in a string"',
        "/* double complex, quoted */",
    ):
        assert prose in frag, (prose, frag)
    assert "/* float complex, quoted in a comment */" in h, h


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_the_upgraded_header_parses_as_cpp(tree, tmp_path):
    """gh-1148's reason for the respell: a C++ caller includes the header.
    Checked on the header as built -- after upgrade then apply -- over a
    copy of the include tree, so a revert by `apply` is what is compiled."""
    root, built, *_ = tree
    inc = tmp_path / "inc"
    shutil.copytree(root / "native" / "inc", inc)
    _textio.write_text(inc / "p" / "g" / "g_core.h", built)
    tu = tmp_path / "use.cpp"
    _textio.write_text(
        tu, '#include "p/g/g_core.h"\nint main() { return 0; }\n'
    )
    r = subprocess.run(
        ["g++", "-std=c++11", "-fsyntax-only", f"-I{inc}", str(tu)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
