"""gh-949: `status` must not claim more than it compared.

`jm status --check` re-applies the manifest to a scratch copy and diffs. `apply`
does not rewrite a create-only file, so those files are byte-identical in both
trees *because neither run touched them* — the diff is empty by construction,
not because they are current. A Makefile missing every target a newer jm ships
reports `OK — up to date`, exit 0.

The exit code is right: `apply` genuinely cannot fix these, and failing would
be worse. What was wrong was the sentence. This is gh-767's rule one step
further out — that established jm must not say "up to date" over files the
generator no longer agrees with; these are files the generator never looked at.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _status  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _status_out(root: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit):
            _status.run(root, check=True)
    return buf.getvalue()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    new_run("p", root)
    object_run(root, "gain", None)
    return root


def test_a_clean_tree_still_reports_ok(tmp_path):
    out = _status_out(_project(tmp_path))
    assert "OK — up to date" in out


def test_the_ok_line_names_what_it_did_not_compare(tmp_path):
    """The claim has to carry its own limit, or it is read as absolute."""
    out = _status_out(_project(tmp_path))
    assert "create-only files are not compared" in out
    # Naming them matters more than the count: a reader has to know whether
    # the file they are about to trust is in this set.
    for name in ("Makefile", ".clang-tidy", "jm_test.h"):
        assert name in out, f"{name} not named in the note"


def test_it_says_so_over_a_provably_stale_makefile(tmp_path):
    """The reported scenario, end to end.

    A Makefile stripped of a target a newer jm ships, `apply` run, and status
    asked. `apply` cannot fix it and the exit code stays 0 — so the note is
    the only thing standing between a reader and the wrong conclusion.
    """
    root = _project(tmp_path)
    mk = root / "Makefile"
    body = mk.read_text(encoding="utf-8")
    assert "\ntidy:" in body, "fixture assumes the tidy target exists"
    i = body.index("\ntidy:")
    j = body.index("\n\nbuild:", i)
    mk.write_text(body[:i] + body[j:], encoding="utf-8")

    out = _status_out(root)
    assert "\ntidy:" not in mk.read_text(encoding="utf-8")
    assert "OK — up to date" in out
    assert "create-only files are not compared" in out
