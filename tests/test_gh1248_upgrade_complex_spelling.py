"""gh-1248: `jm upgrade` migrates a project to the `_Complex` spelling.

gh-1246 changed what jm EMITS. It could not change what an existing project
already contains, and nothing jm shipped could either: `apply` cannot rewrite
the inline `step()` because it lives in the sacred `<comp>_core.h`, and
`regenerate` leaves it for the same reason. Measured on 0.74.0, all three of
`apply`, `regenerate` and `upgrade` left 8 occurrences untouched. The answer in
`docs/upgrading.md` was a `perl -pi -e` block the author ran by hand.

Why `upgrade` and not a new command
-----------------------------------
gh-887 already made `upgrade` the home for "your project is current by schema
number and still needs work" -- it reports stale manifest keys there rather
than claiming "already up to date", because the schema number is not a
compatibility statement. This needs no schema bump either.

Why it REWRITES, where gh-887 refuses to
----------------------------------------
gh-887 states its own reason for reporting rather than rewriting: *"the
replacement can alter what your API does, so it is yours to make, not a
migration's."* `check_return` -> `status_return` changes whether a non-zero
return raises. `complex` -> `_Complex` changes nothing: `complex` is a
`<complex.h>` macro for `_Complex`, so the two are identical tokens after
preprocessing. A migration may rewrite when the rewrite is behaviour-neutral,
and must only report when it encodes a decision.
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

from just_makeit import _upgrade  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

_OLD = "float complex"
_NEW = "float _Complex"


def _project(tmp_path: Path) -> Path:
    """A project as jm emitted them BEFORE gh-1246.

    Built by scaffolding with today's jm and putting the old spelling back,
    rather than by pinning an old jm: the point is a tree that carries the
    pre-gh-1246 text, and reversing the one substitution produces exactly
    that while staying runnable in this suite.
    """
    root = tmp_path / "old"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("old", root, ["nco"], [("gain", "double", "1.0")])
    for path in sorted((root / "native").rglob("*")):
        if path.is_file() and path.suffix in (".c", ".h"):
            if path.name == "clib_common.h":
                continue
            t = path.read_text()
            if _NEW in t:
                path.write_text(t.replace(_NEW, _OLD))
    return root


def _count(root: Path, needle: str) -> int:
    """Occurrences in the project's own C, excluding `clib_common.h`.

    The exclusion mirrors the migration's, and it is the point rather than a
    convenience: that file's comment quotes the old spelling while explaining
    why it was a problem, so counting it would make "the old spelling is gone"
    contradict `test_clib_common_h_is_left_alone` directly below. The first
    cut of this counted it and failed for that reason.
    """
    n = 0
    for path in sorted((root / "native").rglob("*")):
        if path.is_file() and path.suffix in (".c", ".h"):
            if path.name == "clib_common.h":
                continue
            n += path.read_text().count(needle)
    return n


def _upgrade_out(root: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _upgrade.run(root)
    return buf.getvalue()


class TestItMigrates:
    def test_the_old_spelling_is_gone_afterwards(self, tmp_path: Path):
        root = _project(tmp_path)
        assert _count(root, _OLD) > 0, "fixture did not reproduce the old tree"
        _upgrade_out(root)
        assert _count(root, _OLD) == 0

    def test_it_reaches_the_SACRED_header(self, tmp_path: Path):
        """The whole reason this needed a command: the inline `step()` lives
        in `<comp>_core.h`, which `apply` and `regenerate` both refuse."""
        root = _project(tmp_path)
        header = root / "native" / "inc" / "nco" / "nco_core.h"
        assert _OLD in header.read_text()
        _upgrade_out(root)
        assert _OLD not in header.read_text()
        assert _NEW in header.read_text()

    def test_it_names_every_file_it_changed(self, tmp_path: Path):
        out = _upgrade_out(_project(tmp_path))
        assert "respelled the complex types" in out
        assert "native/inc/nco/nco_core.h" in out
        assert "gh-1246" in out

    def test_it_runs_on_a_project_already_at_the_current_schema(
        self, tmp_path: Path
    ):
        """gh-1246 needed no schema bump, so every project this is for is
        already current. Wiring it only into the migration loop would have
        meant it never ran for anybody."""
        out = _upgrade_out(_project(tmp_path))
        assert "already up to date" in out
        assert "respelled" in out


class TestItIsSafeToRunTwice:
    def test_the_second_run_changes_nothing(self, tmp_path: Path):
        root = _project(tmp_path)
        _upgrade_out(root)
        before = {
            p: p.read_text()
            for p in sorted((root / "native").rglob("*"))
            if p.is_file()
        }
        out = _upgrade_out(root)
        after = {
            p: p.read_text()
            for p in sorted((root / "native").rglob("*"))
            if p.is_file()
        }
        assert before == after
        assert "respelled" not in out

    def test_a_fresh_project_is_untouched(self, tmp_path: Path):
        """A project scaffolded by this jm already has the new spelling, so
        the repair must be silent rather than reporting a no-op edit."""
        root = tmp_path / "fresh"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("fresh", root, ["nco"], [("gain", "double", "1.0")])
        assert "respelled" not in _upgrade_out(root)


def test_clib_common_h_is_left_alone(tmp_path: Path) -> None:
    """Its comment QUOTES the old spelling while explaining why that spelling
    was a problem. A blanket pass rewrites that prose into a false statement --
    `_Complex` parses fine from C++ -- which is the "generated code contains
    prose about itself" trap, found by running the documented command in the
    documented order after verifying it in the reverse one."""
    root = _project(tmp_path)
    clib = root / "native" / "inc" / "clib_common.h"
    before = clib.read_text()
    assert _OLD in before, "the comment should quote the old spelling"
    _upgrade_out(root)
    assert clib.read_text() == before


@pytest.mark.skipif(
    not shutil.which("c++") and not shutil.which("g++"),
    reason="no C++ compiler",
)
def test_a_migrated_project_compiles_from_cxx11(tmp_path: Path) -> None:
    """The end-to-end claim, in both include orders.

    Not a proxy for the fix -- this is the thing gh-1246 set out to make
    possible and gh-1248 makes reachable for a project that already exists.
    """
    root = _project(tmp_path)
    _upgrade_out(root)
    cxx = shutil.which("c++") or shutil.which("g++")
    inc = root / "native" / "inc"
    body = (
        "int main(){ std::vector<std::complex<float> > v;"
        " v.push_back(std::complex<float>(3,4)); return 0; }\n"
    )
    orders = {
        "jm-first": '#include "nco/nco_core.h"\n#include <complex>\n'
        "#include <vector>\n" + body,
        "std-first": "#include <complex>\n#include <vector>\n"
        '#include "nco/nco_core.h"\n' + body,
    }
    for name, src in orders.items():
        tu = tmp_path / f"{name}.cpp"
        tu.write_text(src)
        r = subprocess.run(
            [cxx, "-std=c++11", "-fsyntax-only", "-I", str(inc), str(tu)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"{name} did not compile:\n{r.stderr}"
