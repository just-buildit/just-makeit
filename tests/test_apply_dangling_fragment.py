"""gh-327: `jm apply` must not silently promote a former module object —
whose `objects/<obj>.toml` fragment was left behind after the module dropped
it — into a standalone module over its existing (hand-owned) native dir.

A real standalone object's `native/src/<obj>/CMakeLists.txt` builds its own
extension (`Python3_add_library(<obj> MODULE …)`); a module object's carries
only the `<obj>_core` OBJECT lib. apply uses that to tell a *dangling*
former-module fragment from a genuine standalone object: the former is an error
(it would clobber `<obj>_core`), the latter materializes normally.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._split_objects import run as split_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _two_object_module(dest: Path) -> None:
    _silent(new_run, "pkg", dest)
    _silent(module_run, dest, "ddc")
    for obj in ("ddc", "ddcr"):
        _silent(
            object_run,
            dest,
            obj,
            "ddc",
            state_vars=[("g", "double", "1.0")],
        )


def _drop_ddcr_from_module(dest: Path) -> None:
    """Leave the ddcr object section + native/src/ddcr/ in place, but remove
    ddcr from the module's objects list — the exact state that used to promote.

    Layout-agnostic: the module's `objects = [...]` line lives inline in
    just-makeit.toml or in modules/ddc.toml once split.
    """
    for rel in ("just-makeit.toml", "modules/ddc.toml"):
        f = dest / rel
        if f.exists() and 'objects = ["ddc", "ddcr"]' in f.read_text(
            encoding="utf-8"
        ):
            f.write_text(
                f.read_text(encoding="utf-8").replace(
                    'objects = ["ddc", "ddcr"]', 'objects = ["ddc"]'
                ),
                encoding="utf-8",
            )
            return
    raise AssertionError("module objects list not found in any manifest file")


def _make_hand_owned(dest: Path) -> Path:
    """Give native/src/ddcr a hand-owned core lib composing vendored sources —
    the shape apply must refuse to clobber."""
    cml = dest / "native" / "src" / "ddcr" / "CMakeLists.txt"
    cml.write_text(
        cml.read_text(encoding="utf-8").replace(
            "add_library(ddcr_core OBJECT ddcr_core.c)",
            "add_library(ddcr_core OBJECT ddcr_core.c nco_core.c hbdecim.c)",
        ),
        encoding="utf-8",
    )
    return cml


def test_dangling_module_fragment_errors_and_preserves_hand_files(tmp_path):
    dest = tmp_path / "pkg"
    _two_object_module(dest)
    cml = _make_hand_owned(dest)
    before = cml.read_text(encoding="utf-8")
    _drop_ddcr_from_module(dest)

    with pytest.raises(SystemExit) as exc:
        _silent(apply_run, dest)
    assert exc.value.code == 1

    # The hand-owned lib (vendored sources) is untouched, and no standalone
    # glue was scaffolded over it.
    assert cml.read_text(encoding="utf-8") == before
    assert "nco_core.c" in cml.read_text(encoding="utf-8")
    assert not (dest / "native" / "src" / "ddcr" / "ddcr_ext.c").exists()
    assert not (dest / "src" / "pkg" / "ddcr.pyi").exists()


def test_dangling_split_layout_fragment_errors(tmp_path):
    """The issue's exact shape: an `objects/ddcr.toml` fragment (split layout)
    left behind after the module dropped it."""
    dest = tmp_path / "pkg"
    _two_object_module(dest)
    _silent(split_run, dest)  # move object sections into objects/*.toml
    assert (dest / "objects" / "ddcr.toml").exists()
    cml = _make_hand_owned(dest)
    before = cml.read_text(encoding="utf-8")
    _drop_ddcr_from_module(dest)

    with pytest.raises(SystemExit) as exc:
        _silent(apply_run, dest)
    assert exc.value.code == 1
    assert cml.read_text(encoding="utf-8") == before
    assert not (dest / "native" / "src" / "ddcr" / "ddcr_ext.c").exists()


def test_dangling_error_message_names_object_and_resolutions(tmp_path):
    dest = tmp_path / "pkg"
    _two_object_module(dest)
    _make_hand_owned(dest)
    _drop_ddcr_from_module(dest)

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        with pytest.raises(SystemExit):
            _silent(apply_run, dest)
    msg = buf.getvalue()
    assert "ddcr" in msg
    assert "[module.X].objects" in msg
    assert "objects/ddcr.toml" in msg


def test_genuine_standalone_object_is_not_flagged(tmp_path):
    """A real standalone object (its own extension target) materializes
    normally — apply must not mistake it for a dangling fragment."""
    dest = tmp_path / "pkg"
    _silent(new_run, "pkg", dest)
    _silent(
        object_run, dest, "solo", None, state_vars=[("g", "double", "1.0")]
    )
    # Idempotent re-apply over the fully-built standalone object: no error.
    _silent(apply_run, dest)
    assert (dest / "native" / "src" / "solo" / "ddcr_ext.c").exists() is False
    assert (dest / "native" / "src" / "solo" / "solo_ext.c").exists()


def test_fresh_standalone_object_without_native_dir_materializes(tmp_path):
    """A standalone object declared in the manifest but not yet built (no
    native dir) is materialized — nothing on disk to clobber."""
    dest = tmp_path / "pkg"
    _silent(new_run, "pkg", dest)
    _silent(
        object_run, dest, "solo", None, state_vars=[("g", "double", "1.0")]
    )
    import shutil

    shutil.rmtree(dest / "native" / "src" / "solo")
    _silent(apply_run, dest)  # no error — re-creates the standalone object
    cml = dest / "native" / "src" / "solo" / "CMakeLists.txt"
    assert "Python3_add_library(solo" in cml.read_text(encoding="utf-8")
