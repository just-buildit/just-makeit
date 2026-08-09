"""`UNRECONCILED` must say *why*, because its entries are two different things.

gh-848: a pin bump exists to ask "did this upgrade reach my code?", and
nothing answered it. 0.53.0 shipped gh-805 §H; doppler bumped, ran `jm apply`,
and every gate was green — `status --check` 0, no `STALE`, no `UNBUILT`, no
`DROPPED` — while the feature was simply not there. The two fragments that
needed deleting were in `UNRECONCILED` the whole time. So were 73 others that
never will be, and the report was a bare list of paths with no way to tell
them apart. doppler carried "75 unreconciled" unchanged across many pins. It
was wallpaper.

The two kinds:

| kind | means | do |
| --- | --- | --- |
| the author hand-wrote a wrapper body | permanent and correct | nothing, ever |
| the fragment predates a codegen change | a fix you are not receiving | delete + re-apply |

The classification is the *direction* of the difference, which
`_docsync.signature_drift_details` already encodes: something the reference
render has and the fragment lacks is undelivered; a fragment doing more than
jm would is the whole point of a sacred fragment.

What this file does NOT claim: that `ACTIONABLE (0)` means fully up to date.
The comparison sees METH flags, PyArg formats and result-shape markers. A
codegen change that alters a body's internals without touching any of those is
invisible to it, and the report says so rather than implying a guarantee it
cannot make.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _project(root: Path) -> Path:
    """A module object with one plain method, applied and settled."""
    new_run("p", root, [], [])
    module_run(root, "m")
    object_run(root, "w", "m", arg_type="float", return_type="float")
    method_run(root, "w", "close", "m", "void", "int", False, [])
    apply_run(root)
    return root / "native" / "src" / "m" / "m_ext_w.c"


def _report(root: Path) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _status.run(root)
    return buf.getvalue()


def test_a_fragment_predating_a_codegen_change_is_actionable(tmp_path):
    """The manifest moves, the sacred fragment does not: a fix undelivered.

    Simulated the way it actually happens — the manifest gains a result shape
    (`status_return` + a declared error) that the already-written fragment
    cannot have, exactly as a jm upgrade would move the generated body while
    the fragment stayed as written.
    """
    root = tmp_path / "p"
    _project(root)

    cfg = C.load(root)
    for meth in cfg["w"]["methods"]:
        if meth["name"] == "close":
            meth["status_return"] = "true"
            meth["error"] = "ValueError"
            meth["error_message"] = "the capture has a hole"
    C.save(root, cfg)

    out = _report(root)
    assert "ACTIONABLE" in out, (
        "a fragment the manifest has moved past is reported as if the author "
        "wrote it that way, which is the whole defect:\n" + out
    )
    assert "close" in out, f"the actionable member is not named:\n{out}"
    assert "m_ext_w.c" in out, out


def test_a_hand_written_body_is_author_owned(tmp_path):
    """An edited body is permanent, and must not be reported as undelivered.

    The false-positive direction, and the one that would make the split
    worthless: if every unreconciled fragment reads ACTIONABLE, the reader is
    back to a list they cannot act on — 75 entries again, just louder.
    """
    root = tmp_path / "p"
    frag = _project(root)
    frag.write_text(
        frag.read_text().replace("PyObject", "PyObject /* mine */", 1)
    )
    apply_run(root)

    out = _report(root)
    assert "AUTHOR-OWNED" in out, out
    assert "ACTIONABLE" not in out, (
        "a hand-edited body is being reported as an undelivered fix — the "
        "direction of the comparison is inverted:\n" + out
    )


def test_author_owned_paths_stay_listed(tmp_path):
    """The paths are evidence and are not hidden behind a flag.

    Suppressing them was the first cut of this change, on the theory that 73
    paths are noise. `test_an_edited_fragment_is_reported` (gh-767) caught it:
    a project with ONE unreconciled fragment needs to know which one. What
    made the old report wallpaper was the missing reason, not the present
    path.
    """
    root = tmp_path / "p"
    frag = _project(root)
    frag.write_text(
        frag.read_text().replace("PyObject", "PyObject /* mine */", 1)
    )
    apply_run(root)

    assert "m_ext_w.c" in _report(root)


def test_both_kinds_are_separated_in_one_report(tmp_path):
    """The case the bare list could not express: two kinds, one project.

    This is doppler's actual situation — a handful of actionable entries
    buried among many permanent ones — and the reason a count alone would not
    have helped either.
    """
    root = tmp_path / "p"
    frag = _project(root)
    object_run(root, "v", "m", arg_type="float", return_type="float")
    method_run(root, "v", "close", "m", "void", "int", False, [])
    apply_run(root)

    # One object's fragment is hand-edited; the other's manifest moves.
    frag.write_text(
        frag.read_text().replace("PyObject", "PyObject /* mine */", 1)
    )
    cfg = C.load(root)
    for meth in cfg["v"]["methods"]:
        if meth["name"] == "close":
            meth["status_return"] = "true"
            meth["error"] = "ValueError"
            meth["error_message"] = "boom"
    C.save(root, cfg)

    out = _report(root)
    assert "ACTIONABLE" in out and "AUTHOR-OWNED" in out, out
    # The actionable one must be named under ACTIONABLE, not merely present.
    head = out[out.index("ACTIONABLE") : out.index("AUTHOR-OWNED")]
    assert "m_ext_v.c" in head, (
        f"the fragment with an undelivered fix is not under ACTIONABLE:\n{out}"
    )
