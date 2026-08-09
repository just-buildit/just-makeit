"""A `status_allow` entry that matches nothing must say so.

gh-830. `status_allow` exempts a deviation from the gate, and nothing ever
reported that an entry had stopped doing anything — so an exemption outlives
its cause silently. That is gh-823's failure one level up: the entry was added
for a reason, the reason went away, and the allowlist keeps a check switched
off for a file nobody is thinking about any more.

Worse, `_is_allowed` is one matcher over every check, so an entry added to
accept a drifted constructor goes on accepting that path's `stale`/`missing`
classification too. The next genuine divergence under a leftover pattern is
masked by an exemption whose original subject was fixed months earlier.

**Only the unambiguous half is implemented, and this file pins that boundary.**
A pattern matching *no managed file at all* is stale beyond argument — a rename
or a deleted component left it behind. A pattern matching files that merely do
not currently deviate looks stale and often is not: a glob covering a directory
is doing its job by matching clean files, and a pattern kept ahead of a
known-coming change is legitimate. Reporting that one needs an answer to what
`status_allow` is *for* — a burn-down list you are expected to empty, or a
standing statement about files the gate does not govern — and those want
different reports, possibly different keys. That question is still open.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _project(root: Path, allow: "list[str] | None" = None) -> Path:
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    if allow is not None:
        cfg = C.load(root)
        cfg.setdefault("project", {})["status_allow"] = allow
        C.save(root, cfg)
    return root


def _report(root: Path, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            _status.run(root, **kw)
    return buf.getvalue()


def test_a_pattern_matching_nothing_is_reported(tmp_path):
    """The whole point: an entry suppressing nothing says so."""
    out = _report(_project(tmp_path / "p", ["native/src/gone/gone_ext.c"]))
    assert "STALE ALLOW" in out, (
        "an allow entry that matches no managed file is still silent, so an "
        f"exemption outlives its cause with nothing reporting it:\n{out}"
    )
    assert "native/src/gone/gone_ext.c" in out, out


def test_a_pattern_that_matches_is_not_reported(tmp_path):
    """The false-positive direction — a live entry must stay quiet.

    If a working exemption reads as stale, the section is noise and gets
    ignored, which is the state gh-830 is trying to leave.
    """
    root = _project(tmp_path / "p")
    real = next(
        p.relative_to(root).as_posix() for p in (root / "src").rglob("*.pyi")
    )
    out = _report(_project(tmp_path / "q", [real]))
    assert "STALE ALLOW" not in out, (
        f"a pattern matching a real managed file ({real}) was reported as "
        f"stale:\n{out}"
    )


def test_a_glob_over_clean_files_is_not_reported(tmp_path):
    """The deferred half, pinned so it is not implemented by accident.

    A glob matching files that do not currently deviate is NOT reported. It
    looks stale and frequently is not — covering a directory by matching clean
    files is the glob working. Turning this into a finding requires the policy
    answer gh-830 is still waiting on, so if this test ever fails, that
    question was decided somewhere other than the issue.
    """
    out = _report(_project(tmp_path / "p", ["src/**"]))
    assert "STALE ALLOW" not in out, (
        "a glob matching clean files was reported stale — that is the half "
        f"deliberately deferred pending the `status_allow` policy question:\n"
        f"{out}"
    )


def test_no_allow_entries_prints_no_section(tmp_path):
    """A project without the key gains nothing."""
    assert "STALE ALLOW" not in _report(_project(tmp_path / "p"))


def test_it_is_not_counted_as_drift(tmp_path):
    """Reporting only. A stale exemption is not a file `apply` would change.

    Gating on it would fail CI for a manifest hygiene issue and, worse, make
    the report something projects route around rather than read.
    """
    root = _project(tmp_path / "p", ["native/src/gone/gone_ext.c"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = _status.run(root)
    assert rc == 0, (
        f"a stale allow entry was counted as drift (rc={rc}); it is manifest "
        f"hygiene, not a file apply would rewrite:\n{buf.getvalue()}"
    )
