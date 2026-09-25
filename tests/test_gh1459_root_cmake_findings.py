"""gh-1459 / gh-1471: `status` names each root-template fix a project lacks.

The root ``CMakeLists.txt`` is the author's outside jm's marked blocks, so
`apply` never writes a fix made to the rest of the template into an existing
project. gh-1452's libm link and gh-1368's Windows defaults were both lost
that way, under a `status --check` that said nothing. `_rootcmake.FIXES` is
one row per such fix, and `status` prints a ``ROOT CMAKE`` line for each row
the project's file lacks.

Three properties hold it:

- a fresh scaffold reports **nothing**, before and after cmake-format -- a
  row that fired on jm's own render would turn every project's status into
  noise, and the formatter has broken a detector here before (gh-1452);
- removing each row's feature from that render fires **exactly** that row --
  the removal is a regex over the text, independent of the parser under
  test, and every row must have one, so a row added without a sabotage
  fails here rather than shipping unproven;
- the report is wired: printed by `status`, never counted, suppressible per
  fix, carried in ``--json``, and ``--diff`` prints today's render.

GATE: every root-CMakeLists.txt template fix outside jm's managed blocks is
      reported by `status` when the project's file lacks it, and a fresh
      scaffold reports none.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _rootcmake as R


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh1459")
    r = run_cli("new", "p", "--object", "g", cwd=base)
    assert r.returncode == 0, r.stderr
    return base / "p"


def _keys(text: str) -> "list[str]":
    r = R.parse(text)
    return [f.key for f in R.FIXES if f.applies(r) and not f.present(r)]


def _drop(text: str, pattern: str) -> str:
    """Remove the one match of *pattern*; fail loudly on zero or several."""
    found = re.findall(pattern, text, re.S)
    assert len(found) == 1, (pattern, len(found))
    return re.sub(pattern, "", text, count=1, flags=re.S)


# One sabotage per row: the fresh render with that fix's CODE removed. Plain
# regexes over the text, so they do not share a parser with the detector.
SABOTAGE = {
    "libm": r"target_link_libraries\(\s*\$\{lib_target\}\s+PUBLIC[^)]*\)",
    "static-name": r"set_target_properties\(\s*p_lib_static\s+PROPERTIES"
    r"\s+OUTPUT_NAME\s+p_static\s*\)",
    "export-all": r"set_target_properties\(\s*p_lib\s+PROPERTIES"
    r"\s+WINDOWS_EXPORT_ALL_SYMBOLS\s+ON\s*\)",
    "runtime-dest": r"RUNTIME DESTINATION \$\{CMAKE_INSTALL_BINDIR\}",
    "build-type": r"set\(\s*CMAKE_BUILD_TYPE\b[^)]*\)",
    "msvc-runtime": r"set\(\s*CMAKE_MSVC_RUNTIME_LIBRARY\b[^)]*\)",
    "complex-range": r"add_compile_options\(\s*/clang:-fcx-limited-range\s*\)",
    "win-defines": r"\b_USE_MATH_DEFINES\b(?=\))",
    "soversion": r"set_target_properties\(\s*p_lib\s+PROPERTIES\s+VERSION"
    r"[^)]*\)",
    "version-compat": r"(?<=COMPATIBILITY )\$\{JM_VERSION_COMPATIBILITY\}",
    "build-tree-export": r"export\(\s*EXPORT[^)]*\)",
    "pc-paths": r'set\(\s*JM_PC_PREFIX\s+"%JM_INSTALL_PREFIX%"\s*\)',
    # Configure straight to the .pc, which is the pre-tidy shape.
    "pc-tidy": r"(?<=p\.pc)\.raw(?=\s+@ONLY)",
}


def test_every_row_has_a_sabotage():
    assert set(SABOTAGE) == {f.key for f in R.FIXES}


def test_a_fresh_scaffold_reports_nothing(fresh):
    assert R.missing(fresh) == []


def _vertical(text: str) -> str:
    """Every argument on its own line: cmake-format's vertical layout.

    Only whitespace outside quotes and outside comment lines is touched, so
    the result is the same CMake.
    """
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.append(line)
            continue
        parts = re.split(r'("(?:[^"\\]|\\.)*")', line)
        out.append(
            "".join(
                p if i % 2 else re.sub(r"(?<=\S) +(?=\S)", "\n    ", p)
                for i, p in enumerate(parts)
            )
        )
    return "\n".join(out)


def _upper(text: str) -> str:
    """Command names upper-cased: cmake-format's ``command_case: upper``."""
    return re.sub(
        r"^(\s*)([a-z_]+)(\s*\()",
        lambda m: m.group(1) + m.group(2).upper() + m.group(3),
        text,
        flags=re.M,
    )


def _dangle(text: str) -> str:
    """A closing paren on its own line: cmake-format's ``dangle_parens``."""
    return re.sub(r"(?<=\S)\)$", "\n)", text, flags=re.M)


@pytest.mark.parametrize(
    "layout", [_vertical, _upper, _dangle], ids=lambda f: f.__name__
)
def test_a_reformatted_scaffold_reports_nothing(fresh, layout):
    """The render is already cmake-format's output at 79 columns -- the
    template is formatted by the `lint-cmake-format` hook -- so these are
    the OTHER layouts a project's own formatter settings produce. cmakelang
    itself is not importable where `make test` runs, and a second pin of it
    there would be the drift its one pin in pyproject.toml exists to stop.
    """
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    out = layout(text)
    assert out != text
    assert _keys(out) == []
    # And the reflow does not hide a missing fix either.
    assert _keys(layout(_drop(text, SABOTAGE["export-all"]))) == ["export-all"]


@pytest.mark.parametrize("key", sorted(SABOTAGE))
def test_removing_a_fix_reports_exactly_that_fix(fresh, key):
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    assert _keys(_drop(text, SABOTAGE[key])) == [key]


@pytest.mark.parametrize(
    "key, find, replace",
    [
        # The project-wide variable instead of the target property.
        (
            "export-all",
            r"set_target_properties\(\s*p_lib\s+PROPERTIES"
            r"\s+WINDOWS_EXPORT_ALL_SYMBOLS\s+ON\s*\)",
            "set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)",
        ),
        # The -D spelling.
        (
            "win-defines",
            r"add_compile_definitions\([^)]*\)",
            "add_definitions(-D_CRT_SECURE_NO_WARNINGS"
            " -D_CRT_NONSTDC_NO_DEPRECATE -D_USE_MATH_DEFINES)",
        ),
        # A bare `m`, gh-1305's weaker spelling -- it links, so it is not
        # this finding.
        (
            "libm",
            r"target_link_libraries\(\s*\$\{lib_target\}\s+PUBLIC[^)]*\)",
            "target_link_libraries(${lib_target} PUBLIC m)",
        ),
        # gh-1582: the literal a 0.x project would write by hand.
        (
            "version-compat",
            r"(?<=COMPATIBILITY )\$\{JM_VERSION_COMPATIBILITY\}",
            "SameMinorVersion",
        ),
    ],
)
def test_an_equivalent_spelling_is_not_reported(fresh, key, find, replace):
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    assert len(re.findall(find, text, re.S)) == 1
    assert _keys(re.sub(find, lambda _m: replace, text, flags=re.S)) == []


def test_pc_paths_without_the_install_time_write_is_missing(fresh):
    """gh-1582: the variables alone leave the marker in the installed .pc;
    the row needs the install(CODE) that replaces it too."""
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    assert _keys(_drop(text, r"install\(\s*CODE\s*\[\[.*?\]\]\)")) == [
        "pc-paths"
    ]


def test_same_major_version_is_right_from_one_point_oh(fresh):
    """gh-1582: the row is about 0.x; a 1.x project's SameMajorVersion is
    the correct choice, not a finding."""
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    compat = r"(?<=COMPATIBILITY )\$\{JM_VERSION_COMPATIBILITY\}"
    version = r"(?<=VERSION )0\.1\.0\b"
    for pat in (compat, version):
        assert len(re.findall(pat, text)) == 1, pat
    zero = re.sub(compat, "SameMajorVersion", text)
    assert _keys(zero) == ["version-compat"]
    assert _keys(re.sub(version, "1.2.0", zero)) == []


def test_a_static_name_equal_to_the_shared_one_is_the_collision(fresh):
    """Renaming the static library to the project's own name is gh-1368's
    `<name>.lib` collision spelled out, not its fix."""
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    find = SABOTAGE["static-name"]
    assert len(re.findall(find, text, re.S)) == 1
    same = "set_target_properties(p_lib_static PROPERTIES OUTPUT_NAME p)"
    assert _keys(re.sub(find, same, text, flags=re.S)) == ["static-name"]


def test_a_fix_only_in_a_comment_is_still_missing(fresh):
    text = (fresh / "CMakeLists.txt").read_text(encoding="utf-8")
    text = _drop(text, SABOTAGE["export-all"])
    text += "\n# WINDOWS_EXPORT_ALL_SYMBOLS ON\n"
    assert _keys(text) == ["export-all"]


def test_a_variable_nothing_defines_links_nothing():
    """`${JM_MATH_LIBRARY}` with nothing defining it expands to nothing.

    gh-1305's quieter failure: the reference is there and links no libm.
    """
    lib = (
        "project(p LANGUAGES C)\n"
        "add_library(p_lib SHARED a.c)\n"
        "target_link_libraries(p_lib PUBLIC ${JM_MATH_LIBRARY})\n"
    )
    assert "libm" in _keys(lib)
    assert "libm" not in _keys("find_library(JM_MATH_LIBRARY m)\n" + lib)


def test_rows_about_an_absent_library_do_not_apply():
    text = (
        "cmake_minimum_required(VERSION 3.16)\nproject(p LANGUAGES C)\n"
        "add_library(p_lib SHARED a.c)\n"
    )
    keys = _keys(text)
    assert "static-name" not in keys
    assert {"libm", "export-all", "runtime-dest"} <= set(keys)


def test_no_root_file_reports_nothing(tmp_path):
    assert R.missing(tmp_path) == []


# ── the report, through the CLI ──────────────────────────────────────────


@pytest.fixture
def lacking(fresh, tmp_path) -> Path:
    """A copy of the fresh project whose root file lacks the libm fix."""
    import shutil

    proj = tmp_path / "p"
    shutil.copytree(fresh, proj)
    cm = proj / "CMakeLists.txt"
    cm.write_text(
        _drop(cm.read_text(encoding="utf-8"), SABOTAGE["libm"]),
        encoding="utf-8",
    )
    return proj


def test_status_names_it_and_does_not_count_it(fresh, lacking):
    clean = run_cli("status", "--check", cwd=fresh)
    r = run_cli("status", "--check", cwd=lacking)
    assert r.returncode == clean.returncode == 0, r.stdout + r.stderr
    assert "ROOT CMAKE (1)" in r.stdout
    assert re.search(r"^  ↑ libm \(gh-1452\): ", r.stdout, re.M)
    assert "ROOT CMAKE" not in clean.stdout


def test_status_allow_names_one_fix(lacking):
    manifest = lacking / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace(
        "[project]\n", '[project]\nstatus_allow = ["CMakeLists.txt:libm"]\n', 1
    )
    manifest.write_text(text, encoding="utf-8")
    r = run_cli("status", cwd=lacking)
    assert re.search(r"^  ↑ libm .*\[status_allow\]$", r.stdout, re.M)


def test_json_carries_it(lacking):
    r = run_cli("status", "--json", cwd=lacking)
    doc = json.loads(r.stdout[r.stdout.index("{") :])
    assert [e["fix"] for e in doc["root_cmake"]] == ["libm"]
    assert doc["root_cmake"][0]["issue"] == "gh-1452"


def test_diff_prints_the_render_to_merge_from(lacking):
    r = run_cli("status", "--diff", cwd=lacking)
    hunk = r.stdout[r.stdout.index("--- a/CMakeLists.txt") :]
    assert re.search(r"^\+\s+target_link_libraries\(", hunk, re.M)
