"""`create_fn` must rename the CALLEE, not just the caller (gh-1328 repro B).

`create_fn` (gh-509) reached exactly one face: the `tp_init` call in
``_ext.c``. Every C face jm renders kept the derived name, so a scaffold that
declared one did not hold together::

    _ext.c   calls   widget_open      <- create_fn
    _core.c  defines widget_create    <- derived
    _core.h  declares widget_create   <- derived
    test     calls   widget_create    <- derived

which does not compile:

    widget_ext.c:49:20: error: implicit declaration of function 'widget_open'

Four commands from `jm new`, with no hand-editing, against the repo's own
rule that every valid CLI sequence produces a scaffold that builds and passes.

**The same root produced the reported symptom.** On an EXISTING project the
author's `_core.c` already defines the real constructor, so nothing fails to
compile -- instead `apply` renders a reference tree naming `<comp>_create`,
finds no such definition, and appends a placeholder one into the sacred file.
That is a NEW symbol, so it links silently: dead code returning an
uninitialised state, in a file jm's contract says it never writes. doppler
carried three of them (`acq`, `dp_event_log`, `dp_tlm_capture`) and saw them
only through an unrelated bare-`calloc` ratchet.

So this is not two bugs. `create_fn` never reached the C render; whether that
shows up as a build failure or a silent append depends only on whether the
author's constructor is already there.

The assertions below are deliberately toolchain-free -- they read the emitted
sources. `test_it_compiles_and_passes` is the end-to-end proof and skips
without a compiler; these cannot, so the property stays armed everywhere.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

CREATE_FN = "widget_open"
DERIVED = "widget_create"


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(root: Path, create_fn: str | None) -> Path:
    _silent(new_run, "q", root)
    kw = {"create_fn": create_fn} if create_fn else {}
    _silent(
        object_run,
        root,
        "widget",
        None,
        state_vars=[("n", "size_t", "4")],
        **kw,
    )
    return root


def _faces(root: Path) -> dict:
    """The four C faces that must agree on the constructor's name."""
    return {
        "_core.c": root / "native/src/widget/widget_core.c",
        "_core.h": root / "native/inc/widget/widget_core.h",
        "_ext.c": root / "native/src/widget/widget_ext.c",
        "test": root / "native/tests/test_widget_core.c",
    }


class TestTheScaffoldHoldsTogether:
    def test_no_face_names_the_derived_constructor(self, tmp_path):
        """The bug, stated as the property it broke."""
        root = _scaffold(tmp_path / "q", CREATE_FN)
        offenders = {
            name: p.name
            for name, p in _faces(root).items()
            if f"{DERIVED}(" in p.read_text(encoding="utf-8")
        }
        assert not offenders, (
            f"{DERIVED} is not in this project; these faces still name it: "
            f"{offenders}"
        )

    def test_every_face_names_the_declared_constructor(self, tmp_path):
        """Absence is only half of it -- a face that named NOTHING would pass
        the check above. Each one must actually call or declare `create_fn`.
        """
        root = _scaffold(tmp_path / "q", CREATE_FN)
        missing = [
            name
            for name, p in _faces(root).items()
            if f"{CREATE_FN}(" not in p.read_text(encoding="utf-8")
        ]
        assert not missing, f"faces that never name {CREATE_FN}: {missing}"

    def test_the_header_declares_exactly_one_constructor(self, tmp_path):
        """Two declarations is the shape the old test fixture simulated by
        hand, and it is what jm must NOT emit: the author's plus a phantom.
        """
        h = _faces(_scaffold(tmp_path / "q", CREATE_FN))["_core.h"]
        text = h.read_text(encoding="utf-8")
        assert text.count("widget_state_t *widget_") == 1, text

    def test_no_unfilled_slot_reached_any_file(self, tmp_path):
        """`create_name` is a template slot. A path that fails to set it
        writes the token out literally -- which is exactly what the app
        face did until this change, because its writer has no
        unfilled-slot guard the way `_init` does.
        """
        root = _scaffold(tmp_path / "q", CREATE_FN)
        for p in sorted(root.rglob("*.c")) + sorted(root.rglob("*.h")):
            assert "<<" not in p.read_text(encoding="utf-8"), p


class TestTheDefaultIsUnchanged:
    """`create_fn` is an override; without one nothing may move."""

    def test_the_derived_name_is_still_used(self, tmp_path):
        root = _scaffold(tmp_path / "q", None)
        for name, p in _faces(root).items():
            assert f"{DERIVED}(" in p.read_text(encoding="utf-8"), name

    def test_byte_identical_to_the_pre_change_scaffold(self, tmp_path):
        """The slot must collapse to today's text, not merely to today's
        NAME -- a reader of the diff should see no churn for projects that
        never declared a `create_fn`.
        """
        h = _faces(_scaffold(tmp_path / "q", None))["_core.h"]
        assert "widget_state_t *widget_create(size_t n);" in h.read_text(
            encoding="utf-8"
        )


class TestTheDispatchShapesAreUnmoved:
    """`opt_arr_ip` / `dispatch_meta` keep 0.76.2's behaviour exactly.

    Those features select a constructor PER CALL and their "nothing
    supplied" branch calls ``<comp>_create`` literally. Renaming the
    definition without renaming those branches leaves `_ext.c` calling a
    symbol nothing defines -- and `apply` then appends a body for the
    renamed one into the sacred ``_core.c``.

    That is not hypothetical: it is what this change did before this guard,
    measured as an extra ``frame_open(void)`` sitting beside the real
    ``frame_create(size_t n)``. 0.76.2 ignores an object-level ``create_fn``
    outright here, so holding that is a no-op rather than a new refusal.
    Supporting the combination is gh-1335.
    """

    def _frame(self, tmp_path):
        root = tmp_path / "q"
        _silent(new_run, "q", root)
        _silent(
            object_run, root, "frame", None, state_vars=[("n", "size_t", "4")]
        )
        p = root / "just-makeit.toml"
        t = p.read_text(encoding="utf-8")
        t = t.replace("[frame]", '[frame]\ncreate_fn = "frame_open"', 1)
        t += (
            '\n[[frame.init_params]]\nname = "preamble"\n'
            'type = "uint8_t[]"\noptional = true\n'
            'create_fn = "frame_open_pre"\n'
        )
        p.write_text(t, encoding="utf-8")
        return root

    def test_apply_appends_nothing_to_the_sacred_core_c(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = self._frame(tmp_path)
        core = root / "native/src/frame/frame_core.c"
        before = core.read_text(encoding="utf-8")
        _silent(apply_run, root)
        after = core.read_text(encoding="utf-8")
        assert "frame_open(" not in after, (
            "a body for the renamed constructor was appended to a sacred "
            f"file:\n{after[len(before) :]}"
        )
        assert after == before, after

    def test_the_caller_and_callee_still_agree(self, tmp_path):
        """The property that actually matters: whatever name is chosen, both
        sides must choose the same one."""
        root = self._frame(tmp_path)
        core = (root / "native/src/frame/frame_core.c").read_text()
        ext = (root / "native/src/frame/frame_ext.c").read_text()
        assert "frame_create(" in core and "frame_create(" in ext, (core, ext)


@pytest.mark.skipif(
    not shutil.which("cmake")
    or not any(shutil.which(c) for c in ("cc", "gcc", "clang")),
    reason="no C toolchain",
)
class TestItCompilesAndPasses:
    def test_the_create_fn_scaffold_builds(self, tmp_path):
        """The end-to-end form: before this change the build stopped with
        `implicit declaration of function 'widget_open'`.
        """
        root = _scaffold(tmp_path / "q", CREATE_FN)
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from just_makeit._cli import main; main()",
                "build",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        # A zero exit is not proof a compiler ran. Require the artefact --
        # the linked extension module is what "it builds" means, and it is
        # the thing that could not exist while `_ext.c` called a symbol
        # nothing defined.
        built = list(root.rglob("widget*.so"))
        assert built, (
            "build exited 0 but produced no extension module; this check "
            f"was not armed.\n{r.stdout}\n{r.stderr}"
        )
