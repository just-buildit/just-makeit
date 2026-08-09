"""The name check's message must describe the rule the code enforces.

gh-784, split out of gh-625 rather than left in a docstring. gh-625
consolidated five copies of the name check into one `C.valid_identifier`,
preserving semantics byte-for-byte and deliberately — which put the mismatch
the five copies shared in one place where it could finally be decided.

The predicate is `name.replace("_", "").isalnum() and not name[0].isdigit()`.
`str.isalnum()` is Unicode-aware and case-blind, so `Foo`, `café` and `Ωmega`
all passed a message that said **lowercase**.

Two halves, and they are not the same question:

**Uppercase is load-bearing and stays.** A view's class name is legitimately
`CamelCase` and goes through this same predicate (`_view.py`). Rejecting it
would be wrong, so the *message* was the defect — and it is the only thing a
user ever sees.

**Non-ASCII is the half worth rejecting, and is not rejected here.** GCC
accepts UTF-8 identifiers as an extension, MSVC differs, and the `.pyi` is
fine either way — so a name can compile on one toolchain and not another. But
a project carrying one *builds today*, so tightening the predicate outright
would break trees that work. `jm status` reports them for a release first;
`apply` refusing comes later, on its own decision.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def test_the_message_does_not_claim_lowercase():
    """The word that was false. Uppercase passes, so saying otherwise misled.

    This is the entire user-visible defect: the check accepted `Foo` while
    telling anyone who tripped it that only lowercase was allowed.
    """
    msg = C.validate_name("9bad", "object")
    assert msg is not None
    assert "lowercase" not in msg, (
        f"the message still promises a stricter rule than the predicate "
        f"enforces:\n{msg}"
    )


def test_uppercase_is_accepted_and_that_is_deliberate():
    """A view's class name is CamelCase through this same predicate.

    If this ever fails, someone tightened the check to match the old message
    instead of fixing the message — and broke every `jm view`.
    """
    assert C.valid_identifier("Foo")
    assert C.validate_name("BerAlign", "view class") is None


def test_a_digit_start_is_still_rejected():
    """The half of the rule that was always true stays true."""
    assert not C.valid_identifier("9lives")
    assert C.validate_name("9lives", "object") is not None


def test_non_ascii_still_passes_the_predicate(tmp_path):
    """Reported, NOT rejected — the staged migration, pinned.

    If this starts failing, the predicate was tightened. That is a real
    tightening: it breaks projects that build today, so it needs its own
    decision and a release of warning first, not a quiet change here.
    """
    assert C.valid_identifier("café"), (
        "non-ASCII names are now rejected outright — that breaks trees which "
        "build today and skips the reporting release gh-784 asks for"
    )


def test_status_reports_a_non_ascii_name(tmp_path):
    """The migration signal: a project can see it before apply refuses."""
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    cfg = C.load(root)
    cfg["w"]["methods"] = [
        {"name": "café", "arg_type": "void", "return_type": "int"}
    ]
    C.save(root, cfg)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            _status.run(root)
    out = buf.getvalue()
    assert "NON-ASCII" in out, (
        f"a non-ASCII declared name is invisible, so the project gets no "
        f"warning before any future tightening:\n{out}"
    )
    assert "café" in out, out


def test_an_ascii_project_reports_nothing(tmp_path):
    """The common path gains no output."""
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            _status.run(root)
    assert "NON-ASCII" not in buf.getvalue()


def test_it_is_not_counted_as_drift(tmp_path):
    """A name is not a file `apply` would rewrite, so it cannot fail CI.

    The tree is brought into sync first, deliberately. Declaring the method
    and stopping there leaves genuine drift — the `.pyi` and the binding both
    move — and a non-zero count would then prove nothing about the name. The
    first cut of this test asserted `rc == 0` on an unapplied tree and failed
    with `3`, which was the setup talking, not the feature.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    cfg = C.load(root)
    cfg["w"]["methods"] = [
        {"name": "café", "arg_type": "void", "return_type": "int"}
    ]
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = _status.run(root)
    assert "NON-ASCII" in buf.getvalue(), (
        "the name stopped being reported once the tree was in sync, so the "
        "report depends on unrelated drift rather than on the name"
    )
    assert rc == 0, (
        f"a non-ASCII name was counted as drift (rc={rc}); it is a naming "
        f"advisory, not a file apply would rewrite:\n{buf.getvalue()}"
    )
