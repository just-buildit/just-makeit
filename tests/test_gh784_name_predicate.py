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

**Non-ASCII is the half worth rejecting, and is now rejected.** GCC accepts
UTF-8 identifiers as an extension, MSVC differs, and the `.pyi` is fine either
way — so a name can compile on one toolchain and not another, arriving through
a name instead of through `[project] platforms`. A project carrying one
*builds today*, so this could not be a same-day tightening: `jm status` listed
every non-ASCII declared name for a release (v0.55.0) first, and the
`isascii()` term landed after.

The tightening reaches further than the command line, which is the part worth
pinning: `jm apply` replays a manifest through the same declaration commands,
so a tree that already carries `café` is refused too. `jm status` therefore
prints the rename list **before** its own scratch replay — otherwise the
report exists only for projects that do not need it, and the one project that
does gets a single `error:` per run and a rename-recompile-repeat loop.
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
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _legacy_declare(root, toml_text):
    """Append a declaration the way an OLDER jm left it on disk.

    Every test below that models "a tree already carrying a non-ASCII name"
    used to build it with `C.save`. Since gh-910 that is the one thing which
    cannot produce this state: `save` is the gate, so a manifest carrying such
    a name is by definition one that never passed through today's `save` — an
    older jm wrote it. Going through `save` would be modelling a tree that
    cannot exist, and the tests would have been pinning the gate's absence.
    """
    path = root / C.FILENAME
    path.write_text(path.read_text() + toml_text, encoding="utf-8")


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


def test_non_ascii_is_rejected(tmp_path):
    """The second half, landed. This test used to assert the opposite.

    It pinned the permissive behaviour on purpose — so that tightening the
    predicate could not happen quietly, only as a decision someone had to
    come here and reverse. The reporting release it was waiting for is
    v0.55.0 (`jm status` has listed every non-ASCII declared name since), so
    the condition it guarded is met and the assertion is inverted rather
    than deleted: the same line now holds the rule the other way.
    """
    assert not C.valid_identifier("café")
    assert not C.valid_identifier("Ωmega")
    assert not C.valid_identifier("naïve_gain")


def test_the_message_says_why_a_word_made_of_letters_was_refused():
    """`café` satisfies the generic message, so printing it says nothing.

    "Use ASCII letters, digits, and underscores only; must not start with a
    digit" is a rule `café` does not visibly break — every character is a
    letter. A user who typed it needs the term that actually rejected it, or
    the error reads as a bug in jm.
    """
    msg = C.validate_name("café", "method")
    assert msg is not None
    assert "ASCII" in msg, msg
    assert "MSVC" in msg, (
        f"the message refuses the name without naming the portability "
        f"reason, which is the only thing that makes it actionable:\n{msg}"
    )


def test_the_module_id_message_explains_it_too():
    """The peer message over the same predicate.

    gh-784's first half had to be fixed twice because two messages sit over
    `valid_identifier`; the second half inherits exactly that shape. A dotted
    id is rejected segment-by-segment, so the non-ASCII segment is what the
    explanation must follow.
    """
    assert C.validate_module_id("café") is not None
    msg = C.validate_module_id("dsp.filtrés")
    assert msg is not None
    assert "MSVC" in msg, (
        f"the module message rejects a non-ASCII segment with the generic "
        f"wording, leaving half the users who hit this unhelped:\n{msg}"
    )


def test_the_command_that_would_declare_one_now_refuses(tmp_path, capsys):
    """End to end, because the predicate alone proves nothing reaches it.

    `require_name` is what turns the predicate into a refusal, and gh-625
    exists because two commands never called it. A unit test on
    `valid_identifier` passes just as happily when no command consults it.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    capsys.readouterr()
    with pytest.raises(SystemExit):
        method_run(root, "w", "café", None, "void", "int", False, [])
    err = capsys.readouterr().err
    assert "not a valid method name" in err, err
    assert C.methods(C.load(root), "w") == [], (
        "the command exited non-zero but had already written the name into "
        "the manifest"
    )


def test_status_reports_a_non_ascii_name(tmp_path):
    """The migration signal, on the tree that now needs it most.

    `status` replays `apply` on a scratch copy to compute drift, and that
    replay refuses this manifest — so the report has to be printed before it,
    or the only project carrying a non-ASCII name is the only one that never
    sees the list of names to rename. `SystemExit` here is expected and is
    not what this test is about; the output written before it is.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    _legacy_declare(
        root,
        '\n[[w.methods]]\nname = "café"\n'
        'arg_type = "void"\nreturn_type = "int"\n',
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            with contextlib.suppress(SystemExit):
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


def test_every_kind_that_reaches_generated_c_is_reported():
    """The report is only a migration story if it names ALL of them.

    A staged tightening makes one promise: rename what `status` lists and
    `apply` will not refuse you later. A report covering some declaration
    kinds and not others breaks exactly that promise — the project renames
    what it was shown, ships, and is refused anyway for a name it was never
    told about. Partial coverage is worse than none, because it reads as a
    clean bill of health.

    Functions, state fields and init params were the three missing kinds.
    All are written into the **sacred** header — a function's C symbol, a
    state field's struct member, an init param's `create()` parameter — so
    each carries the same GCC-accepts-it/MSVC-may-not trap that put the
    other six on the list.
    """
    cfg = {
        "project": {"name": "p"},
        "module": {"m": {"objects": ["w"], "functions": [{"name": "café"}]}},
        "w": {
            "state": [{"name": "gaïn", "type": "double", "default": "1.0"}],
            "init_params": [{"name": "tempÖ", "type": "double"}],
        },
    }
    reported = dict((name, kind) for kind, name in C.non_ascii_names(cfg))
    assert reported.get("café") == "function", (
        f"a non-ASCII module function is invisible to the report: {reported}"
    )
    assert reported.get("gaïn") == "state field", (
        f"a non-ASCII state field is invisible to the report: {reported}"
    )
    assert reported.get("tempÖ") == "init param", (
        f"a non-ASCII init param is invisible to the report: {reported}"
    )


def test_status_reports_a_non_ascii_function_name(tmp_path):
    """End to end, because the unit above cannot prove the wiring.

    `non_ascii_names` walking a hand-built dict says nothing about whether a
    real manifest stores functions where the walk looks for them.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        module_run(root, "m")
        object_run(root, "w", "m", arg_type="float", return_type="float")
    _legacy_declare(
        root, '\n[[module.m.functions]]\nname = "café"\ndoc = ""\n'
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            with contextlib.suppress(SystemExit):
                _status.run(root)
    out = buf.getvalue()
    # Anchored on the report's own line format, not on the name appearing
    # somewhere in the output. Declaring the function also creates drift, so
    # `status` prints `+ native/src/m/café.c` under MISSING — a substring
    # check for the name alone passes with the feature entirely removed.
    assert "  ? function 'café'" in out, (
        f"a declared non-ASCII function name never reaches the report:\n{out}"
    )


def test_the_module_message_does_not_claim_lowercase_either():
    """The peer copy of the message gh-784's first half fixed.

    `validate_name` stopped saying "lowercase" because `valid_identifier`
    accepts `Foo`. `validate_module_id` calls that same predicate and kept
    the same false word, so `jm object --module Foo` is accepted while the
    error text for a bad sibling still promises it would not be. Two
    messages over one predicate is the peer-implementation shape: fixing
    one and not the other leaves the defect fully intact for half the users
    who hit it.
    """
    msg = C.validate_module_id("9bad")
    assert msg is not None
    assert "lowercase" not in msg, (
        f"the module message still promises a stricter rule than the "
        f"shared predicate enforces:\n{msg}"
    )
    assert C.validate_module_id("Foo") is None, (
        "uppercase module ids are accepted by the predicate; if this fails "
        "the message was made true by tightening, which is the other half "
        "of gh-784 and needs its own decision"
    )


def test_apply_refuses_a_manifest_that_already_carries_one(tmp_path, capsys):
    """The reach the predicate alone does not show, measured not assumed.

    `apply` replays a manifest through the same declaration commands, so the
    `isascii()` term lands on names already written, not only on newly typed
    ones — a tree carrying `café` stops applying at this version. That is the
    tightening gh-784 asks for (a name only GCC accepts does not become safe
    by being old), and it is the whole reason the `status` report had to move
    ahead of the replay.

    This test used to be `test_it_is_not_counted_as_drift`, asserting the
    opposite end of the same setup: apply succeeds, `status` returns 0, a
    name is an advisory and never CI-red. It cannot both be true and is worth
    the note — the drift question it pinned is now unreachable, because the
    manifest never gets as far as producing files to compare.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
    _legacy_declare(
        root,
        '\n[[w.methods]]\nname = "café"\n'
        'arg_type = "void"\nreturn_type = "int"\n',
    )

    capsys.readouterr()
    with pytest.raises(SystemExit):
        apply_run(root)
    assert "not a valid method name" in capsys.readouterr().err


def test_status_names_every_offender_before_the_replay_kills_it(tmp_path):
    """One run must hand over the whole rename list, not the first name.

    The replay refuses at the first bad name, so a report printed after it
    would be a rename-recompile-repeat loop over a manifest whose remaining
    offenders are never named. Two objects here, because one cannot tell the
    difference between "prints the list" and "prints the name apply happened
    to die on".
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
        object_run(root, "v", None, arg_type="float", return_type="float")
    _legacy_declare(
        root,
        '\n[[w.methods]]\nname = "café"\n'
        'arg_type = "void"\nreturn_type = "int"\n'
        '\n[[v.methods]]\nname = "\u03a9mega"\n'
        'arg_type = "void"\nreturn_type = "int"\n',
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            with contextlib.suppress(SystemExit):
                _status.run(root)
    out = buf.getvalue()
    assert "café" in out and "Ωmega" in out, (
        f"the report reached only as far as the replay let it, so renaming "
        f"what it lists still leaves the manifest refused:\n{out}"
    )
