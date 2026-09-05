"""gh-1277: a view's ctor is compared against the parent's ACTUAL constructor.

The check refusing a view whose ``create_fn`` matches its parent's compared
against the **default-derived** ``<obj>_create``, while its own message claimed
to be comparing against the parent's. gh-509 lets an object back its
``tp_init`` with a differently named C function, and this check never learned
about it — so under an override, ``<obj>_create`` stayed the one name a view
could not use even though it was no longer the parent's.

Why that mattered rather than merely reading oddly: it blocks the shape where
**the general constructor is the base and the specialised one is the flavor**.
``exclude_properties``/``exclude_methods`` trim a *view*, never a parent, so a
surface that must appear on only one of the two front doors has to sit on the
base, with the trimmed one as the view. If the trimmed one is the object's
historical ``<obj>_create``, that arrangement was unreachable.

doppler's `frame` is the case: a deferred builder (``frame_create_desc`` —
extend the description, then ``build()``) and a materialising four-field
constructor (``frame_create``). All nine builder verbs refuse on a materialised
frame, so they belong to the builder alone; that requires the materialising
class to be the view, which requires the view to use ``frame_create``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402


@pytest.fixture()
def project(tmp_path, capsys):
    """One module object, no view yet."""
    dest = tmp_path / "proj"
    new_run("proj", dest, modules=["wfm"])
    object_run(
        dest,
        "frame",
        module="wfm",
        arg_type="void",
        return_type="void",
        no_state=True,
        no_step=True,
        no_reset=True,
    )
    capsys.readouterr()
    return dest


def _override_parent_ctor(project, fn):
    """Point the object's `tp_init` at a differently named C ctor (gh-509).

    The class name moves too: the base defaults to `Frame`, and the whole
    arrangement under test is the one where `Frame` becomes the VIEW.
    """
    cfg = C.load(project)
    cfg["frame"]["create_fn"] = fn
    cfg["frame"]["class_name"] = "FrameBuilder"
    C.save(project, cfg)


def test_a_view_may_use_obj_create_when_the_parent_overrode_it(
    project, capsys
):
    """The case that was refused, and is the point of the issue.

    With the parent on `frame_create_desc`, `frame_create` is a genuinely
    different constructor and must be available to the view.
    """
    _override_parent_ctor(project, "frame_create_desc")
    view_run(project, "frame", "Frame", "wfm", "frame_create")
    capsys.readouterr()

    views = C.views(C.load(project), "frame")
    assert [v["class_name"] for v in views] == ["Frame"]
    assert views[0]["create_fn"] == "frame_create"


def test_colliding_with_the_overridden_parent_is_still_refused(
    project, capsys
):
    """The check's intent survives: a view builds from a DIFFERENT ctor.

    Without this the fix would have widened the check into nothing — the
    failure mode is a view that registers a second class over the identical
    constructor.
    """
    _override_parent_ctor(project, "frame_create_desc")
    with pytest.raises(SystemExit):
        view_run(project, "frame", "Frame", "wfm", "frame_create_desc")
    err = capsys.readouterr().err
    assert "must differ from the parent's" in err
    assert "frame_create_desc" in err


def test_the_message_names_the_parents_actual_ctor(project, capsys):
    """It claimed to name the parent's and named the default instead."""
    _override_parent_ctor(project, "frame_create_desc")
    with pytest.raises(SystemExit):
        view_run(project, "frame", "Frame", "wfm", "frame_create_desc")
    err = capsys.readouterr().err
    assert "'frame_create_desc'" in err
    # The bug in one assertion: it used to name the default here.
    assert "'frame_create'" not in err


def test_without_an_override_obj_create_is_still_refused(project, capsys):
    """Byte-identical behaviour for every project that never set create_fn."""
    cfg = C.load(project)
    cfg["frame"]["class_name"] = "FrameBuilder"
    C.save(project, cfg)
    with pytest.raises(SystemExit):
        view_run(project, "frame", "Frame", "wfm", "frame_create")
    err = capsys.readouterr().err
    assert "'frame_create'" in err
