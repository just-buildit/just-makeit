"""`jm status` must say why, when the apply it runs internally refuses.

gh-886. `status` copies the tree to a scratch dir and runs `apply` on it,
suppressing that apply's output so it can print its own report. That is right
for *progress* output and wrong for a *fatal* one: when the replay refused, its
`error:` line went into a `StringIO` discarded with the frame and its
`sys.exit(1)` propagated out through `status`. The user got two warnings, no
report, exit 1, and nothing saying why.

Found by running the gh-848 `UNRECONCILED` split against a real doppler
checkout pinned at 0.33.15 — the tree that motivated gh-848 in the first place.
It produced no report at all, and the reason was unreachable without running
`apply` by hand.

The situation matters more than the exit code. `status` is what you reach for
when a tree is in an unknown state — an old pin, a half-finished migration, a
bump you are assessing — and those are exactly the trees whose internal apply
is most likely to refuse. So the diagnostic was unavailable precisely where it
was needed, failing in the least informative way available.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _project_apply_refuses(root: Path) -> Path:
    """A tree whose manifest `apply` rejects.

    Uses the doppler shape: a method declaring `error` without the
    `status_return` / `error_negative` that gives a failing return something to
    be translated from. doppler reached it by carrying `check_return`, a
    function-only key this jm no longer honours on a method (gh-887), which
    leaves `error` orphaned in exactly this way.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
        method_run(root, "w", "close", None, "void", "int", False, [])
    cfg = C.load(root)
    for meth in cfg["w"]["methods"]:
        if meth["name"] == "close":
            meth["error"] = "ValueError"
            meth["error_message"] = "boom"
    C.save(root, cfg)
    return root


def _status_stderr(root: Path) -> tuple[str, int]:
    """Run status, returning (stderr, exit code). Exit 0 when it completed."""
    err = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(err):
            try:
                _status.run(root)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    return err.getvalue(), code


def test_the_reason_reaches_the_user(tmp_path):
    """The refusal's own message must appear, not just a bare exit."""
    out, code = _status_stderr(_project_apply_refuses(tmp_path / "p"))
    assert code != 0, "a refused replay must still be a failure"
    assert "--error names the exception" in out, (
        "status swallowed the reason the internal apply refused, which is the "
        f"whole defect — the user sees an exit code and nothing else:\n{out}"
    )


def test_it_says_the_real_tree_was_not_touched(tmp_path):
    """ "apply failed" reads as "my project was modified" unless told.

    The replay runs on a scratch copy and `status` never writes to the real
    tree. Reporting an apply failure without saying so invites the reader to
    go looking for damage that does not exist.
    """
    out, _ = _status_stderr(_project_apply_refuses(tmp_path / "p"))
    assert "NOT modified" in out, out


def test_it_names_which_apply_failed(tmp_path):
    """The failure is attributed to the internal replay, not to `status`.

    Without this the message reads as though `status` itself is broken, and
    the next step — run `apply` and look — is not obvious.
    """
    out, _ = _status_stderr(_project_apply_refuses(tmp_path / "p"))
    assert "internal `apply` replay" in out, out
    assert "just-makeit apply" in out, (
        f"the message does not point at the command that shows the full "
        f"output:\n{out}"
    )


def test_a_healthy_project_is_unaffected(tmp_path):
    """The common path must not gain output, or every run pays for this.

    Progress suppression is still correct and still happens; only a fatal is
    let through.
    """
    root = tmp_path / "ok"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")

    out, code = _status_stderr(root)
    assert code == 0, f"a healthy project should not exit non-zero:\n{out}"
    assert "internal `apply` replay" not in out, (
        f"the fatal path fired on a project that applies cleanly:\n{out}"
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_the_failure_is_reported_in_both_output_modes(tmp_path, as_json):
    """`--json` must not reintroduce the silent exit.

    `status` returns before its text rendering when emitting JSON, so a fix
    placed in the reporting path rather than around the replay would leave
    exactly one mode silent — and it would be the machine-readable one a CI
    job depends on.
    """
    root = _project_apply_refuses(tmp_path / "p")
    err = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(err):
            try:
                _status.run(root, as_json=as_json)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    assert code != 0
    assert "--error names the exception" in err.getvalue(), err.getvalue()
