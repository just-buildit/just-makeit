"""gh-1949: an object named for its module configures, builds and passes.

``jm module dsp.filters`` then ``jm object filters --module dsp.filters``
exited 0 and left a project ``cmake`` refused to configure:
``add_library cannot create target "filters_core" because another target
with the same name already exists``. An object's C lives in
``native/src/<obj>/`` and a module's in ``native/src/<cname>/``, so only the
object named for the module's CNAME shares the module's directory and core
(the collocated object). The module's CMakeLists asked the LEAF instead: it
prepended ``filters``'s core, test and bench to ``native/src/dsp_filters/``
while the object's own directory declared them too -- and, taking the
object for the module's core, dropped ``dsp_filters_core``, which the root
CMakeLists still named. `apply` asked a third spelling, the dotted id, which
no object name can equal, so the genuinely collocated ``dsp_filters`` went
stale. `_config.collocated_object` is now the one answer every writer asks.

GATE: for a flat and a dotted module, an object named for each of the
module's name roles that is an identifier -- ``ModulePaths``'s fields, read
here, not listed: the leaf and the cname, one string when flat -- gets a
project that `jm test` configures, builds, and passes in C and in Python,
on both paths to that tree: the CLI (`jm module`, `jm object --module`) and
`apply` (a fresh `jm new` given the same manifest).

The verb-versus-`apply` half -- `status --check` clean, `apply` a no-op, the
incremental tree the from-scratch one -- is the gh-2057 gate's, on its
``collocated``, ``dotted-leaf`` and ``dotted-cname`` shapes. This file holds
what that gate cannot see: on main `apply` dropped the dangling
``$<TARGET_OBJECTS:>`` line and both oracles went quiet, and the tree still
did not configure.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit._upgrade import _manifest_fragments
from test_gh1109_seeded_construction_is_attempted import _pytest_counts

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: A flat module and a dotted one: the two forms whose name roles differ.
MODULES = ("filt", "dsp.filt")


def object_names(module: str) -> "list[str]":
    """Every name *module* derives that an object could also be called.

    Read off `ModulePaths`, so a name role added there is walked here
    without an edit; the dotted id and the path are not identifiers and
    drop out by the rule `jm object` itself applies.
    """
    mp = C.module_paths(module)
    return sorted(
        {
            value
            for value in mp
            if isinstance(value, str) and C.valid_identifier(value)
        }
    )


def _runs() -> list:
    return [
        pytest.param(m, o, id=f"{m}-{o}")
        for m in MODULES
        for o in object_names(m)
    ]


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (list(argv), (r.stdout + r.stderr)[-3000:])


def _cli_path(base: Path, module: str, obj: str) -> Path:
    _ok(base, "new", "p")
    root = base / "p"
    _ok(root, "module", module)
    _ok(root, "object", obj, "--module", module)
    return root


def _apply_path(base: Path, module: str, obj: str) -> Path:
    """The same manifest, rendered by `apply` into a fresh `jm new`."""
    for sub in ("declared", "fresh"):
        (base / sub).mkdir()
    declared = _cli_path(base / "declared", module, obj)
    _ok(base / "fresh", "new", "p")
    root = base / "fresh" / "p"
    for path in _manifest_fragments(root):
        path.unlink()
    for path in (declared / C.FILENAME, *_manifest_fragments(declared)):
        dest = root / path.relative_to(declared)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    _ok(root, "apply")
    return root


def test_both_name_roles_are_walked():
    """The case list is not empty and reaches the collision: a dotted
    module's leaf and its cname are different objects to name."""
    assert object_names("filt") == ["filt"]
    assert object_names("dsp.filt") == ["dsp_filt", "filt"]


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize(
    "path", [_cli_path, _apply_path], ids=["cli", "apply"]
)
@pytest.mark.parametrize(("module", "obj"), _runs())
def test_the_project_builds_and_passes(tmp_path, path, module, obj):
    root = path(tmp_path, module, obj)
    out = run_cli("test", cwd=root)
    assert out.returncode == 0, (out.stdout + out.stderr)[-6000:]
    # The object's own C test ran, and its generated Python suite: a build
    # that compiled nothing of it cannot pass here.
    assert f"test_{obj}_core" in out.stdout, out.stdout[-3000:]
    counts = _pytest_counts(out.stdout)
    assert counts.get("passed", 0) > 0, out.stdout[-3000:]
    assert not counts.get("failed") and not counts.get("skipped"), counts
