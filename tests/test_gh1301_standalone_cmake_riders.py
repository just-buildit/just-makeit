"""`jm apply` keeps an author's riders on a standalone object's CMakeLists.

gh-1301. gh-275 taught apply to leave a module object's per-object
``CMakeLists.txt`` alone once it carries build rules the manifest cannot
express, and gh-271 to carry the author's ``if(VAR)`` blocks across a
re-render. The standalone object's file went through a blind overwrite
instead, so the same three edits survived in a module and vanished
standalone -- the vendored source first, as a link error one command later
in whatever consumed its symbols.

Both paths now share one reconcile, so this file asks every rider of both
peers: a check that walked only the standalone path would pass for a fix
that moved the defect across.

The trap worth pinning is the obvious wrong fix. The standalone template
emits a POST_BUILD ``add_custom_command`` of its own, so reusing gh-275's
presence test unchanged calls every plain ``jm object`` hand-owned and
freezes its glue. `test_a_plain_file_still_takes_manifest_changes` is what
goes red for that.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(dest: Path, module: str | None) -> Path:
    """One object ``o``, standalone or in module ``m``; its CMakeLists."""
    _silent(new_run, "p", dest)
    if module:
        _silent(module_run, dest, module)
    _silent(
        object_run,
        dest,
        "o",
        module=module,
        state_vars=[("n", "int", "0")],
    )
    _silent(apply_run, dest)
    return dest / "native" / "src" / "o" / "CMakeLists.txt"


def _vendored_source(text: str) -> str:
    old = "add_library(o_core OBJECT o_core.c)"
    assert text.count(old) == 1, text
    return text.replace(old, "add_library(o_core OBJECT o_core.c vendored.c)")


#: Each edit the manifest cannot express, and so jm must not drop.
RIDERS = {
    "vendored_source": _vendored_source,
    "source_property": lambda t: (
        t
        + "\nset_source_files_properties(vendored.c PROPERTIES COMPILE_FLAGS -w)\n"
    ),
    "external_block": lambda t: (
        t
        + "\nif(FOO_LIB)\n  target_link_libraries(o_core PUBLIC ${FOO_LIB})\n"
        "endif()\n"
    ),
}

FACES = [pytest.param(None, id="standalone"), pytest.param("m", id="module")]


@pytest.mark.parametrize("module", FACES)
@pytest.mark.parametrize("rider", sorted(RIDERS))
def test_apply_keeps_the_rider(tmp_path, module, rider):
    cml = _scaffold(tmp_path / "p", module)
    edited = RIDERS[rider](cml.read_text(encoding="utf-8"))
    cml.write_text(edited, encoding="utf-8")
    _silent(apply_run, tmp_path / "p")
    assert cml.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize("module", FACES)
@pytest.mark.parametrize("rider", sorted(RIDERS))
def test_status_check_is_clean_with_the_rider(tmp_path, module, rider):
    """A kept rider is not drift: `status` replays the same reconcile."""
    dest = tmp_path / "p"
    cml = _scaffold(dest, module)
    cml.write_text(
        RIDERS[rider](cml.read_text(encoding="utf-8")), encoding="utf-8"
    )
    assert _silent(_status.run, dest, check=True) == 0


def test_a_plain_file_still_takes_manifest_changes(tmp_path):
    """The template's own POST_BUILD step must not read as hand-owned.

    A manifest edit has to reach a plain standalone CMakeLists, or protecting
    riders has frozen the glue it was meant to preserve.
    """
    dest = tmp_path / "p"
    cml = _scaffold(dest, None)
    assert "add_custom_command" in cml.read_text(encoding="utf-8")
    cfg = C.load(dest)
    cfg["o"]["extra_link_libs"] = ["foo_lib"]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    assert "foo_lib" in cml.read_text(encoding="utf-8")


def test_status_allow_is_still_honoured(tmp_path):
    """Leaving the blind overwrite must not drop gh-441's opt-out with it."""
    dest = tmp_path / "p"
    cml = _scaffold(dest, None)
    cfg = C.load(dest)
    cfg["project"]["status_allow"] = ["native/src/o/CMakeLists.txt"]
    cfg["o"]["extra_link_libs"] = ["foo_lib"]
    C.save(dest, cfg)
    before = cml.read_text(encoding="utf-8")
    _silent(apply_run, dest)
    assert cml.read_text(encoding="utf-8") == before
