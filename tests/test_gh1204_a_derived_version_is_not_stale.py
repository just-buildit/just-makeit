"""gh-1204: a version the build derives is not a version that drifted.

gh-1141's check compares each generated copy of `[project] version` as a
literal string. Deriving the value instead — `PROJECT_NUMBER = $(PKG_VERSION)`,
with the build exporting it — is the *strongest* fix for the drift that check
exists to catch: it cannot go stale, whereas syncing the number merely
postpones the next drift to the next release.

The check reported that as drift, and it is a `!` finding that fails
`jm status --check`. So a project that fixed the bug properly got a permanently
red gate whose only remedy was to go back to a hardcoded number that will drift
again. That inverts the incentive the check exists to create, and a finding
nobody can clear teaches people to ignore the gate faster than the self-healing
one `_createonly` already warns about.

A whitelist, not a list of syntaxes
-----------------------------------
The question is "is this a version", and every way of writing "no, it is
computed" answers it identically — `$(VAR)`, `@VAR@`, `${VAR}`. PEP 440
requires a version to begin with a digit, so that is the whole test.

It also makes three paths deliberate that were previously right by accident or
wrong by omission: `_CMAKE_RE` already required a leading digit, `_lib_c_re`
requires a quoted literal and so skipped `return PKG_VERSION;` without meaning
to, and `_DOXY_RE` matched `\\S+` and so reported it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _projversion as V  # noqa: E402

CFG = {"project": {"name": "commz", "version": "1.1.2a47"}}
STALE = "0.1.0"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A tree whose every version copy is stale, so a skip is visible as an
    absence from a non-empty list rather than from an empty one."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "commz"\nversion = "{STALE}"\n', encoding="utf-8"
    )
    (tmp_path / "CMakeLists.txt").write_text(
        f"project(commz\n  VERSION {STALE}\n  LANGUAGES C)\n", encoding="utf-8"
    )
    (tmp_path / "native" / "src").mkdir(parents=True)
    (tmp_path / "native" / "src" / "commz_lib.c").write_text(
        f'const char *commz_version(void) {{ return "{STALE}"; }}\n',
        encoding="utf-8",
    )
    return tmp_path


def _rels(root: Path) -> set:
    return {c.rel for c in V.drift(root, CFG)}


def _doxyfile(root: Path, value: str) -> None:
    (root / "Doxyfile").write_text(
        f"PROJECT_NUMBER         = {value}\n", encoding="utf-8"
    )


class TestTheCheckStillWorks:
    """The half that must not be weakened. gh-1141 found a project at
    `1.1.2a47` whose `<pkg>_version()` had returned `0.1.0` since scaffolding,
    and that has to keep being found."""

    def test_a_stale_literal_is_reported(self, root: Path) -> None:
        _doxyfile(root, STALE)
        assert "Doxyfile" in _rels(root)

    def test_every_other_stale_copy_too(self, root: Path) -> None:
        assert _rels(root) >= {
            "pyproject.toml",
            "CMakeLists.txt",
            "native/src/commz_lib.c",
        }

    def test_a_matching_literal_is_not_reported(self, root: Path) -> None:
        _doxyfile(root, "1.1.2a47")
        assert "Doxyfile" not in _rels(root)


class TestADerivedValueIsSkipped:
    @pytest.mark.parametrize(
        "value",
        [
            "$(COMMZ_VERSION)",  # doxygen, and make
            "@COMMZ_VERSION@",  # cmake configure_file
            "${COMMZ_VERSION}",  # cmake, and shell
            "%COMMZ_VERSION%",  # windows batch
        ],
    )
    def test_it(self, root: Path, value: str) -> None:
        _doxyfile(root, value)
        assert "Doxyfile" not in _rels(root), value

    def test_the_others_are_still_reported_beside_it(self, root: Path) -> None:
        """A skip has to be one file, not a switch that quiets the check."""
        _doxyfile(root, "$(COMMZ_VERSION)")
        assert _rels(root) == {
            "pyproject.toml",
            "CMakeLists.txt",
            "native/src/commz_lib.c",
        }


class TestTheOtherTwoPathsAreDeliberate:
    """The issue's ask: make the three consistent, rather than leaving one
    right by accident and one wrong by omission."""

    def test_the_lib_c_derived_form_is_skipped(self, root: Path) -> None:
        """`return COMMZ_VERSION;` — injected through
        `target_compile_definitions`. It was already skipped, because the
        pattern demands a quoted literal, but by accident rather than
        by decision."""
        (root / "native" / "src" / "commz_lib.c").write_text(
            "const char *commz_version(void) { return COMMZ_VERSION; }\n",
            encoding="utf-8",
        )
        assert "native/src/commz_lib.c" not in _rels(root)

    def test_the_cmake_derived_form_is_skipped(self, root: Path) -> None:
        (root / "CMakeLists.txt").write_text(
            "project(commz\n  VERSION ${COMMZ_VERSION}\n  LANGUAGES C)\n",
            encoding="utf-8",
        )
        assert "CMakeLists.txt" not in _rels(root)


class TestThePredicate:
    """Asserted directly, because it is the whole decision."""

    @pytest.mark.parametrize(
        "value,is_literal",
        [
            ("1.1.2a47", True),
            ("0.1.0", True),
            ("1!2.0", True),  # PEP 440 epoch
            ("$(V)", False),
            ("@V@", False),
            ("${V}", False),
            ("%V%", False),
            ("PKG_VERSION", False),
            ("", False),
        ],
    )
    def test_it(self, value: str, is_literal: bool) -> None:
        assert bool(V._VERSION_LITERAL.match(value)) is is_literal

    def test_it_is_a_whitelist_not_a_syntax_list(self) -> None:
        """A blacklist of expansion syntaxes has to grow with every build
        system anyone uses. This one does not: whatever the spelling, it does
        not start with a digit."""
        assert V._VERSION_LITERAL.pattern == r"^[0-9]"
        assert not re.search(r"\$|@|\{", V._VERSION_LITERAL.pattern)
