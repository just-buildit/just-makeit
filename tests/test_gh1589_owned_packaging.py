"""gh-1589: jm owns the packaging templates, so a packaging fix reaches a project.

``cmake/<pkg>.pc.in`` and ``cmake/<pkg>-config.cmake.in`` hold no authored
content -- every value arrives through a CMake variable the root CMakeLists
sets -- but they were create-only. So every fix to either (gh-1576's
``@JM_PC_CFLAGS@``, gh-1582's install-time prefix and optional fields)
reached new projects only; an existing one got an OUTDATED line and a hand
diff.

They are now born carrying the ownership token, through the same writer as
the scaffolded test (gh-1489) and benchmark (gh-1528), and `apply` renders
them whole while the token names the file. A project scaffolded before has no
token: `status` names each such template that is behind (PACKAGING), and
`jm adopt --packaging` hands them to jm -- refusing a template whose adoption
would drop a line the render does not keep, unless accepted.

GATE: jm renders an owned packaging template on apply, never writes one whose
      token was deleted, reports a token-less one that is behind, and adopts
      one only when nothing would be lost or the loss is accepted -- and
      `adopt --packaging --check` writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from _jmrun import run_cli

PC = Path("cmake/p.pc.in")
CONFIG = Path("cmake/p-config.cmake.in")


def _project(tmp_path: Path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "p"


def _unown(root: Path, rel: Path, *, edit=lambda s: s) -> None:
    """Make *rel* what a project scaffolded before gh-1589 has: no token."""
    from just_makeit import _render as R

    path = root / rel
    text = path.read_text(encoding="utf-8")
    head = R.owned_token(rel.name) + "\n" + R.OWNED_PACKAGING_NOTE
    assert text.startswith(head), text[:200]
    path.write_text(edit(text[len(head) :]), encoding="utf-8")


def _older_cflags(text: str) -> str:
    """The Cflags line as jm rendered it before gh-1579 added a slot."""
    old = text.replace(
        "Cflags: -I${includedir}@JM_PC_ROW_CFLAGS@", "Cflags: -I${includedir}"
    )
    assert old != text
    return old


def test_both_templates_are_born_owned(tmp_path):
    root = _project(tmp_path)
    for rel in (PC, CONFIG):
        first = (root / rel).read_text(encoding="utf-8").splitlines()[0]
        assert first == f"# jm:generated {rel.name}", first


def test_apply_restores_an_edited_owned_template(tmp_path):
    root = _project(tmp_path)
    before = {rel: (root / rel).read_text() for rel in (PC, CONFIG)}
    for rel in (PC, CONFIG):
        with open(root / rel, "a", encoding="utf-8") as f:
            f.write("HAND EDIT\n")
    r = run_cli("status", cwd=root)
    assert "cmake/p.pc.in" in r.stdout and "STALE" in r.stdout, r.stdout
    assert run_cli("apply", cwd=root).returncode == 0
    for rel in (PC, CONFIG):
        assert (root / rel).read_text() == before[rel], rel


def test_a_template_whose_token_was_deleted_is_never_written(tmp_path):
    root = _project(tmp_path)
    _unown(root, PC, edit=lambda s: s + "# mine now\n")
    mine = (root / PC).read_text()
    assert run_cli("apply", cwd=root).returncode == 0
    assert (root / PC).read_text() == mine


def test_a_fresh_scaffold_reports_no_packaging(tmp_path):
    root = _project(tmp_path)
    r = run_cli("status", cwd=root)
    assert "PACKAGING" not in r.stdout, r.stdout


def test_status_names_a_template_that_is_behind(tmp_path):
    root = _project(tmp_path)
    _unown(root, PC, edit=_older_cflags)
    r = run_cli("status", cwd=root)
    assert "PACKAGING (1)" in r.stdout, r.stdout
    assert "  ↑ cmake/p.pc.in\n" in r.stdout, r.stdout
    assert "jm adopt --packaging" in r.stdout
    assert "1 packaging" in r.stdout
    # A token-less template that is today's render otherwise is not behind.
    _unown(root, CONFIG)
    r = run_cli("status", cwd=root)
    assert "p-config.cmake.in" not in r.stdout, r.stdout
    j = json.loads(run_cli("status", "--json", cwd=root).stdout)
    assert [e["path"] for e in j["packaging"]] == ["cmake/p.pc.in"], j


def test_check_writes_nothing_and_shows_the_diff(tmp_path):
    root = _project(tmp_path)
    _unown(root, PC, edit=_older_cflags)
    _unown(root, CONFIG, edit=lambda s: s + "set(MY_HAND 1)\n")
    before = {rel: (root / rel).read_bytes() for rel in (PC, CONFIG)}
    r = run_cli("adopt", "--packaging", "--check", cwd=root)
    assert r.returncode == 1, r.stdout
    assert "would adopt         cmake/p.pc.in" in r.stdout, r.stdout
    # An older jm's line the render only extends is not "lost".
    assert "would drop: Cflags" not in r.stdout, r.stdout
    assert "+Cflags: -I${includedir}@JM_PC_ROW_CFLAGS@" in r.stdout, r.stdout
    assert "REFUSES             cmake/p-config.cmake.in" in r.stdout
    assert "would drop: set(MY_HAND 1)" in r.stdout
    assert {rel: (root / rel).read_bytes() for rel in (PC, CONFIG)} == before


def test_adopt_takes_what_loses_nothing_and_refuses_the_rest(tmp_path):
    root = _project(tmp_path)
    _unown(root, PC, edit=_older_cflags)
    _unown(root, CONFIG, edit=lambda s: s + "set(MY_HAND 1)\n")
    hand = (root / CONFIG).read_bytes()
    r = run_cli("adopt", "--packaging", cwd=root)
    assert r.returncode == 1, r.stdout
    pc = (root / PC).read_text()
    assert pc.startswith("# jm:generated p.pc.in\n"), pc
    assert "@JM_PC_ROW_CFLAGS@" in pc
    assert (root / CONFIG).read_bytes() == hand
    # Owned now: apply renders it, and status has nothing to report for it.
    assert "cmake/p.pc.in" not in run_cli("status", cwd=root).stdout


def test_accept_takes_a_refused_template(tmp_path):
    root = _project(tmp_path)
    _unown(root, CONFIG, edit=lambda s: s + "set(MY_HAND 1)\n")
    r = run_cli("adopt", "--packaging", "--accept", CONFIG.name, cwd=root)
    assert r.returncode == 0, r.stdout
    text = (root / CONFIG).read_text()
    assert text.startswith("# jm:generated p-config.cmake.in\n"), text
    assert "MY_HAND" not in text


def test_packaging_is_not_mixed_with_fragment_targets(tmp_path):
    root = _project(tmp_path)
    r = run_cli("adopt", "--packaging", "g", cwd=root)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--packaging takes no objects" in r.stderr


def test_adopt_help_is_usage_not_an_error(tmp_path):
    """gh-1569's first papercut."""
    root = _project(tmp_path)
    r = run_cli("adopt", "--help", cwd=root)
    assert r.returncode == 0, r.stderr
    assert "adopt --packaging" in r.stdout
