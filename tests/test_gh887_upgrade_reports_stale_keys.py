"""`jm upgrade` must not call a manifest "up to date" that `apply` will refuse.

gh-887. doppler sat at `schema = "7"` — genuinely current — while carrying
`check_return` on a method, a function-only key this jm no longer honours.
`upgrade` printed `already up to date (schema 7)`, changed nothing, and the
next `jm apply` refused with:

    error: --error names the exception a failing return raises, so it needs
           --error-negative or status_return as well.

...which names neither the key, nor the file, nor `check_return`. The same run
of `upgrade` had *already printed a warning* saying exactly what was wrong. The
connection between the two was left to the reader, across two commands.

The schema version genuinely was current, and that is the defect: **a schema
number is not a compatibility statement**, while "up to date" is read as one.
Same shape as gh-830 and gh-823 — a check reporting clean because it answers a
narrower question than the one asked.

**Reported, never rewritten**, and the tests below pin that. The replacement is
mechanically trivial and the warning already states it — but `check_return` ->
`status_return` changes whether a non-zero return becomes an exception. A
schema migration moves jm-owned structure; silently editing a *declaration*
changes what the project says its own API does, and a migration that quietly
alters runtime behaviour is worse than one that refuses.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _upgrade
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _project(root: Path, *, stale: bool) -> Path:
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        object_run(root, "w", None, arg_type="float", return_type="float")
        method_run(root, "w", "close", None, "void", "int", False, [])
    if stale:
        cfg = C.load(root)
        for meth in cfg["w"]["methods"]:
            if meth["name"] == "close":
                # A function-only key on a method: doppler's exact shape.
                meth["check_return"] = "true"
        C.save(root, cfg)
    return root


def _upgrade_out(root: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            _upgrade.run(root)
    return buf.getvalue()


def test_a_stale_key_is_not_called_up_to_date(tmp_path):
    """The headline claim must not be made when it is false."""
    out = _upgrade_out(_project(tmp_path / "p", stale=True))
    assert "already up to date" not in out, (
        "upgrade still claims the manifest is ready while carrying a key "
        f"`apply` will refuse:\n{out}"
    )
    assert "Not up to date" in out, out


def test_it_names_the_key_and_the_replacement(tmp_path):
    """Naming the object is not enough — the fix must be in the output."""
    out = _upgrade_out(_project(tmp_path / "p", stale=True))
    assert "check_return" in out, f"the offending key is not named:\n{out}"
    assert "status_return" in out, (
        f"the replacement is not named, so the reader is sent looking:\n{out}"
    )


def test_it_points_at_apply(tmp_path):
    """Connect this command to the failure the user is about to hit.

    That connection is the whole gap: the warning and the refusal were printed
    by different commands, and nothing said they were the same problem.
    """
    out = _upgrade_out(_project(tmp_path / "p", stale=True))
    assert "apply" in out and "refuse" in out, out


def test_it_does_not_rewrite_the_manifest(tmp_path):
    """The decision, pinned. `upgrade` reports; the author edits.

    If this ever fails, someone made `check_return` -> `status_return`
    automatic — which changes whether a non-zero return raises, silently, on a
    pin bump. That is a runtime behaviour change wearing a migration's clothes.
    """
    root = _project(tmp_path / "p", stale=True)
    before = (root / C.FILENAME).read_text()
    _upgrade_out(root)
    assert (root / C.FILENAME).read_text() == before, (
        "upgrade rewrote the manifest instead of reporting; the replacement "
        "alters what a failing return does and belongs to the author"
    )
    cfg = C.load(root)
    assert any(
        m.get("check_return")
        for m in cfg["w"]["methods"]
        if m["name"] == "close"
    ), "the stale key was removed rather than reported"


def test_a_clean_manifest_still_reports_up_to_date(tmp_path):
    """The common path is unchanged, or every project pays for this."""
    out = _upgrade_out(_project(tmp_path / "p", stale=False))
    assert "already up to date" in out, out
    assert "Not up to date" not in out, out


def test_the_owning_fragment_is_named_when_split(tmp_path):
    """Split layouts get a path, since "which object" is not "which file".

    A project with forty fragment files told only the object name is left
    doing the search this report exists to save.
    """
    root = _project(tmp_path / "p", stale=True)
    frag_dir = root / "objects"
    frag_dir.mkdir(exist_ok=True)
    (frag_dir / "w.toml").write_text("# split fragment\n")

    out = _upgrade_out(root)
    assert "objects/w.toml" in out, (
        f"the owning fragment exists but is not named:\n{out}"
    )


def test_no_path_is_invented_when_the_fragment_is_absent(tmp_path):
    """A wrong path is worse than none, so it is derived by existence."""
    out = _upgrade_out(_project(tmp_path / "p", stale=True))
    assert "objects/" not in out, (
        f"a fragment path was printed for a single-file manifest:\n{out}"
    )
