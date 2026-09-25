"""gh-1589: the root CMakeLists's install section is jm's managed block.

Every packaging fix of epic gh-1584 -- the soname, the install-time ``.pc``
prefix, the macOS install name, the build-tree export, 0.x version matching
-- lives in the install section of the root ``CMakeLists.txt``. That file is
the author's outside its marked blocks, so before this each fix reached a new
project and never an existing one; `status` could only name them, one
``ROOT CMAKE`` row per fix.

The section is now a block from ``# ── Install`` to ``# ── End install``,
rendered by `apply` like the external-deps block, and those rows retire in
favour of one (``install-block``: the file has no managed block). The block
is compared as CMake COMMANDS, not bytes, so a project's formatter reflowing
it is not a rewrite.

GATE: the root CMakeLists's install section is rendered by `apply` from the
      template between its two sentinels, compared by command so a
      formatter's layout is never rewritten; a hand edit inside it is
      reported stale and then restored; a file without the end sentinel, and
      everything outside the block, is never written; and no packaging
      command sits outside the block in the template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _rootcmake as R
from just_makeit import _render as T


@pytest.fixture
def proj(tmp_path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "p"


def _root(proj: Path) -> Path:
    return proj / "CMakeLists.txt"


def _block(text: str) -> str:
    span = R.install_block(text)
    assert span is not None
    return text[span[0] : span[1]]


def _edit(path: Path, find: str, replace: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(find) == 1, find
    path.write_text(text.replace(find, replace), encoding="utf-8")


def test_a_fresh_scaffold_carries_the_block(proj):
    text = _root(proj).read_text(encoding="utf-8")
    assert R.install_block(text) is not None
    assert R.missing(proj) == []


def test_a_hand_edit_inside_the_block_is_reported_then_restored(proj):
    root = _root(proj)
    before = root.read_text(encoding="utf-8")
    _edit(
        root,
        "export(\n  EXPORT p-targets",
        "set(X 1)\nexport(\n  EXPORT p-targets",
    )
    check = run_cli("status", "--check", cwd=proj)
    assert check.returncode == 1, check.stdout
    status = run_cli("status", cwd=proj)
    assert "~ CMakeLists.txt" in status.stdout, status.stdout
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert root.read_text(encoding="utf-8") == before


def test_a_reflowed_block_is_not_rewritten(proj):
    root = _root(proj)
    text = root.read_text(encoding="utf-8")
    s, e = R.install_block(text)
    # Upper-case commands and one argument per line: what a project's
    # cmake-format settings produce. Same commands, different bytes. Only
    # commands that start a line OUTSIDE the install(CODE [[...]]) script:
    # upper-casing that script's `file(` would change what it runs.
    reflowed = re.sub(
        r"^(include|install|set|export|if|else|endif|configure_file"
        r"|configure_package_config_file|write_basic_package_version_file"
        r"|set_target_properties)\(",
        lambda m: m.group(1).upper() + "(",
        text[s:e],
        flags=re.M,
    ).replace(" DESTINATION ", "\n    DESTINATION ")
    assert reflowed != text[s:e]
    assert R.calls(reflowed) != [] and [
        (c.name, c.args) for c in R.calls(reflowed)
    ] == [(c.name, c.args) for c in R.calls(text[s:e])]
    root.write_text(text[:s] + reflowed + text[e:], encoding="utf-8")
    kept = root.read_text(encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert root.read_text(encoding="utf-8") == kept


def test_without_the_end_sentinel_the_section_is_the_authors(proj):
    root = _root(proj)
    text = root.read_text(encoding="utf-8")
    end = re.search(r"^# ── End install[^\n]*\n", text, re.M)
    assert end
    theirs = (
        text[: end.start()].replace(
            "export(\n  EXPORT p-targets",
            "# mine\nexport(\n  EXPORT p-targets",
        )
        + text[end.end() :]
    )
    root.write_text(theirs, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert root.read_text(encoding="utf-8") == theirs
    assert [f.key for f in R.missing(proj)] == ["install-block"]
    status = run_cli("status", cwd=proj)
    assert "install-block (gh-1589)" in status.stdout, status.stdout


def test_what_follows_the_block_is_never_written(proj):
    root = _root(proj)
    mine = "install(FILES README.md DESTINATION share/doc/p)\n"
    root.write_text(root.read_text(encoding="utf-8") + mine, encoding="utf-8")
    _edit(
        root,
        "export(\n  EXPORT p-targets",
        "set(X 1)\nexport(\n  EXPORT p-targets",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    after = root.read_text(encoding="utf-8")
    assert after.endswith(mine)
    assert "set(X 1)" not in after


#: The commands the retired ROOT CMAKE rows asked about. They are safe to
#: retire only while every one of them is rendered INSIDE the block.
_PACKAGING = re.compile(
    r"^\s*(install|export|configure_package_config_file"
    r"|write_basic_package_version_file)\(|SOVERSION|INSTALL_NAME_DIR"
    r"|\.pc\.in|JM_PC_",
    re.M,
)


def test_no_packaging_command_is_rendered_outside_the_block():
    text = T.CMAKE_LISTS_TOP
    s, e = R.install_block(text)
    outside = text[:s] + text[e:]
    hits = [
        m.group(0) for m in _PACKAGING.finditer(R._strip_comments(outside))
    ]
    assert hits == [], hits
    assert _PACKAGING.search(R._strip_comments(text[s:e]))


def test_the_history_table_knows_every_command_the_template_renders():
    """`adopt --packaging` keeps an install section whose every command some
    released jm rendered. The table only knows the commands recorded in it,
    so a template change that renders a new one must record it
    (`make install-history-update`) -- or the NEXT release's projects would
    be told their jm-rendered section holds commands of their own."""
    from just_makeit import _installhistory as H

    s, e = R.install_block(T.CMAKE_LISTS_TOP)
    current = {(c.name, c.args) for c in R.calls(T.CMAKE_LISTS_TOP[s:e])}
    missing = sorted(current - H.CALLS)
    assert missing == [], (
        "the root install section renders commands the history table lacks;"
        " run `make install-history-update`:\n" + "\n".join(map(repr, missing))
    )


# ── adopting an older project's install section ─────────────────────────────

FROZEN = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "stale_project"
    / "tree"
)


@pytest.fixture
def stale(tmp_path) -> Path:
    """A project 0.33.14 scaffolded: its install section runs to the end of
    the file, with no end sentinel -- a real old render, not a made-up one."""
    import shutil

    proj = tmp_path / "stale"
    shutil.copytree(FROZEN, proj)
    assert R.install_block(_root(proj).read_text(encoding="utf-8")) is None
    return proj


def test_an_old_section_every_released_jm_rendered_adopts(stale):
    root = _root(stale)
    before = root.read_text(encoding="utf-8")
    check = run_cli("adopt", "--packaging", "--check", cwd=stale)
    assert check.returncode == 0, check.stdout
    assert "would adopt         CMakeLists.txt" in check.stdout, check.stdout
    assert root.read_text(encoding="utf-8") == before  # --check writes nothing

    r = run_cli("adopt", "--packaging", cwd=stale)
    assert r.returncode == 0, r.stdout + r.stderr
    after = root.read_text(encoding="utf-8")
    assert R.install_block(after) is not None
    # What precedes the section is the author's, untouched.
    head = before[: R.INSTALL_BEGIN.search(before).start()]
    assert after.startswith(head)
    # The block is today's: apply finds nothing to change in it.
    assert "install-block" not in [f.key for f in R.missing(stale)]
    again = run_cli("adopt", "--packaging", "--check", cwd=stale)
    assert "jm's already        CMakeLists.txt" in again.stdout, again.stdout


def test_a_command_no_released_jm_rendered_is_refused_and_named(stale):
    root = _root(stale)
    mine = "include(cmake/packaging.cmake)\n"
    root.write_text(root.read_text(encoding="utf-8") + mine, encoding="utf-8")
    before = root.read_text(encoding="utf-8")
    r = run_cli("adopt", "--packaging", cwd=stale)
    assert r.returncode == 1, r.stdout
    assert "REFUSES             CMakeLists.txt" in r.stdout, r.stdout
    assert "would drop: include(cmake/packaging.cmake)" in r.stdout, r.stdout
    assert "End install" in r.stdout  # says where the author's rules go
    assert root.read_text(encoding="utf-8") == before  # nothing written

    ok = run_cli(
        "adopt", "--packaging", "--accept", "CMakeLists.txt", cwd=stale
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert R.install_block(root.read_text(encoding="utf-8")) is not None
