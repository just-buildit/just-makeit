"""gh-942: every translation unit jm generates must reach the compile database.

The bug this replaces (gh-939) was a hand-rolled `compile_commands.json`: jm
enumerated a fixed set of source shapes — `<comp>_core.c`, `<comp>_ext.c`, the
test, the bench, `<mod>_ext.c`, `<mod>_core.c` — and wrote them out itself.
Every source kind added after that list was frozen went missing, and the nine
commands that write C without calling the writer left the file stale on top.
Nothing noticed for as long as it existed, because the only test asserted
`.exists()`.

Handing the job to cmake fixes today's misses and cannot go stale, but it does
not close the hole — it moves it. A `.c` file jm generates that no CMake target
references is now absent from the database *and* silently unbuilt, which is a
worse failure wearing the same face. So the compiler tier below compares the
database against the tree, not against a list.

Two tiers, matching how this repo already splits build-dependent tests:

- the wiring tier needs no toolchain and runs everywhere
- the compiler tier configures a real cmake project and reads what it emitted
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as T  # noqa: E402
from just_makeit._app import run as app_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: `#include "foo.c"` — a .c pulled textually into another TU rather than
#: compiled on its own. Anchored to the quoted form: the angle-bracket
#: spelling never names a project source.
_INCLUDED_C = re.compile(r'^\s*#\s*include\s+"([^"]+\.c)"', re.MULTILINE)

_CMAKE = shutil.which("cmake")
_CC = shutil.which("cc") or shutil.which("gcc")
_needs_build = pytest.mark.skipif(
    _CMAKE is None or _CC is None,
    reason="needs cmake and a C compiler",
)


def _scaffold(dest: Path) -> Path:
    """Build the widest source shape jm can produce, in one project.

    Wider than the bug needs, deliberately. The hand-rolled writer missed the
    package lib TU outright and never saw `jm app`'s executable at all, but
    the module here also produces `#include`d binding fragments — the case
    that made the first draft of this test fail on correct output.
    """
    new_run("ccproj", dest)
    module_run(dest, "filt")
    object_run(dest, "fir", "filt")
    object_run(dest, "iir", "filt")
    object_run(dest, "gain", None)
    app_run(dest, target="c", object_="gain")
    return dest


# ── wiring tier (no toolchain) ───────────────────────────────────────────────


class TestScaffoldWiring:
    def test_jm_writes_no_compile_database(self, tmp_path):
        """gh-939: cmake owns this file, not jm."""
        root = _scaffold(tmp_path / "ccproj")
        assert not (root / "compile_commands.json").exists()

    def test_scaffold_ships_clang_tidy(self, tmp_path):
        """gh-941: the config lands in the tree it was written for."""
        root = _scaffold(tmp_path / "ccproj")
        tidy = root / ".clang-tidy"
        assert tidy.exists()
        # The regex names this project's header tree. A config carrying some
        # other project's layout filters out everything and reports clean,
        # which is how jm's own root copy sat dead for a year.
        assert "HeaderFilterRegex:" in tidy.read_text()
        assert (root / "native" / "inc").is_dir()

    def test_header_filter_covers_every_tree_jm_writes_headers_into(
        self, tmp_path
    ):
        """Derived, not spelled out — a new header tree must not slip past.

        This asserted the literal `"native/inc/.*"` until gh-934 added
        `native/tests/jm_test.h`, at which point the literal was still true
        and the property it stood for was false: two shared harness headers
        (jm_bench.h, jm_test.h) sat outside the filter and were exempt from
        every check in the config. A filter that excludes a directory the
        generator uses is a check that covers less than it claims.

        So match the regex against the headers actually on disk rather than
        against a string someone has to remember to update.
        """
        root = _scaffold(tmp_path / "ccproj")
        text = (root / ".clang-tidy").read_text(encoding="utf-8")
        pattern = re.search(
            r'^HeaderFilterRegex:\s*"(.+)"\s*$', text, re.MULTILINE
        ).group(1)
        rx = re.compile(pattern)

        headers = sorted(root.glob("native/**/*.h"))
        assert headers, "scaffold produced no headers at all"
        uncovered = [
            str(h.relative_to(root))
            for h in headers
            if not rx.search(str(h.relative_to(root)))
        ]
        assert not uncovered, (
            "generated headers excluded from clang-tidy by HeaderFilterRegex "
            f"{pattern!r}: {uncovered}"
        )

    def test_clang_tidy_ships_only_where_a_target_runs_it(self, tmp_path):
        """A config with no runner is the bug, not the fix (gh-941).

        The `make` build system emits no compile database, so clang-tidy
        cannot run there and the file must not appear.
        """
        root = tmp_path / "simple"
        new_run("simple", root, build_system="make")
        assert not (root / ".clang-tidy").exists()
        assert "tidy:" not in (root / "Makefile").read_text()

    def test_compile_commands_target_is_phony_and_documented(self):
        """gh-940: no timestamp left to get wrong, and it is reachable."""
        mk = T.MAKEFILE

        # The old rule was `compile_commands.json: $(BUILD_DIR)/CMakeCache.txt`
        # — keyed on a file that does not move when the source list does.
        assert "compile_commands.json: $(BUILD_DIR)/CMakeCache.txt" not in mk

        phony = next(ln for ln in mk.splitlines() if ln.startswith(".PHONY:"))
        # The declaration wraps, so take the continuation with it.
        idx = mk.index(phony)
        decl = mk[idx : mk.index("\n\n", idx)]

        # Scoped to the `help:` RECIPE, not the whole file. Searching all of
        # mk for "make tidy" passed with the help line deleted, because the
        # header comment at the top of the template lists the same targets —
        # the file contains prose about itself, so an unanchored match reads
        # the documentation of the thing instead of the thing.
        help_body = mk[mk.index("\nhelp:") :]

        for target in ("compile-commands", "tidy", "coverage"):
            assert target in decl, f"{target} missing from .PHONY"
            assert f"\n{target}:" in mk, f"{target} has no rule"
            assert f"make {target}" in help_body, (
                f"{target} undocumented in help"
            )

    def test_tidy_depends_on_a_fresh_database(self):
        """Running tidy against last week's database lints last week."""
        mk = T.MAKEFILE
        rule = next(ln for ln in mk.splitlines() if ln.startswith("tidy:"))
        assert "compile-commands" in rule


# ── compiler tier ────────────────────────────────────────────────────────────


@_needs_build
class TestCompileDatabaseCoverage:
    def test_every_generated_tu_is_in_the_database(self, tmp_path):
        root = _scaffold(tmp_path / "ccproj")
        build = root / "build"
        cfg = subprocess.run(
            [
                "cmake",
                "-S",
                str(root),
                "-B",
                str(build),
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            capture_output=True,
            text=True,
        )
        if cfg.returncode != 0:
            pytest.skip(f"cmake configure unavailable here:\n{cfg.stderr}")

        db = build / "compile_commands.json"
        assert db.exists(), "cmake emitted no compile database"
        listed = {
            Path(e["file"]).resolve()
            for e in json.loads(db.read_text(encoding="utf-8"))
        }
        present = {p.resolve() for p in root.glob("native/**/*.c")}

        # A per-object binding fragment is `#include`d into its module's
        # _ext.c, so it is not a translation unit and correctly has no entry
        # of its own — clangd and clang-tidy reach it through the parent.
        # The first draft of this test asserted every .c was in the database
        # and flagged both fragments; the invariant is that a .c is EITHER
        # compiled OR textually included, never neither.
        included = {
            (c.parent / m).resolve()
            for c in present
            for m in _INCLUDED_C.findall(c.read_text(encoding="utf-8"))
        }

        orphans = sorted(
            str(p.relative_to(root)) for p in present - listed - included
        )
        assert not orphans, (
            "generated C reached by nothing — no compile database entry and "
            f"no .c includes it, so it is never built: {orphans}"
        )

        # Guard the carve-out itself: if the fragments stop being included,
        # the exemption above must stop applying rather than quietly widen.
        assert included, (
            "no #include'd .c found — is the exemption still real?"
        )
