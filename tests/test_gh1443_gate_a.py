"""gh-1443 Gate A, checked on itself: each check fires on a planted defect.

`_downstream_gates` runs over every example, and it is only worth anything
if each of its checks can go red. An example run cannot show that: a clean
example and a check that looks at nothing read the same. So each check is
given a scaffold with exactly its defect planted, and must report it -- and
the ratchet must refuse both a new finding and a stale line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _downstream_gates as G
from _jmrun import run_cli


@pytest.fixture
def root(tmp_path: Path) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    r = run_cli(
        "object",
        "gain",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=tmp_path / "p",
    )
    assert r.returncode == 0, r.stderr
    return tmp_path


def _kinds(found: "set[str]") -> "set[str]":
    return {f.split("\t")[1] for f in found}


def test_status_check_failing_is_a_finding(root: Path) -> None:
    (root / "p" / "src" / "p" / "gain.pyi").write_text("# edited\n")
    found = G.findings(root)
    assert any(f.startswith("p\tstatus --check\t") for f in found), found


def test_a_reapply_that_warns_is_a_finding(root: Path) -> None:
    cml = root / "p" / "CMakeLists.txt"
    text = cml.read_text(encoding="utf-8")
    assert "# ── Components" in text
    cml.write_text(text.replace("# ── Components", "# (removed)"))
    found = G.findings(root)
    assert any(
        f.startswith("p\tre-apply warns\t") and "Components" in f
        for f in found
    ), found


def test_a_reapply_that_writes_is_a_finding(root: Path) -> None:
    (root / "p" / "src" / "p" / "gain.pyi").unlink()
    found = G.findings(root)
    assert "p\tre-apply writes\tcreate src/p/gain.pyi" in found, found


def test_ruff_check_and_format_are_findings(root: Path) -> None:
    (root / "p" / "src" / "p" / "planted.py").write_text(
        "import os,sys\nx=1\n"
    )
    found = G.findings(root)
    assert "p\truff check\tsrc/p/planted.py E401" in found, found
    assert "p\truff format\tsrc/p/planted.py" in found, found


def test_findings_carry_no_temp_path(root: Path) -> None:
    (root / "p" / "src" / "p" / "gain.pyi").unlink()
    assert not any(str(root) in f for f in G.findings(root))


def test_the_ratchet_refuses_new_findings_and_stale_lines(
    root: Path, tmp_path_factory, monkeypatch
) -> None:
    ratchets = tmp_path_factory.mktemp("gate_a")
    monkeypatch.setattr(G, "RATCHET_DIR", ratchets)
    monkeypatch.setattr(G, "UPDATE", False)
    monkeypatch.setattr(G, "SHRINK", True)
    # A fresh scaffold has no findings of its own since gh-1478, and this
    # needs at least one to drop from the file: plant two.
    (root / "p" / "src" / "p" / "planted.py").write_text("import os,sys\n")
    found = G.findings(root)
    assert len(found) >= 2, found

    (ratchets / "ex.txt").write_text("".join(f"{f}\n" for f in found))
    G.check("ex", root)  # exactly recorded: passes

    (ratchets / "ex.txt").write_text(
        "".join(f"{f}\n" for f in sorted(found)[1:])
    )
    with pytest.raises(AssertionError, match="did not fail before"):
        G.check("ex", root)

    (ratchets / "ex.txt").write_text(
        "".join(f"{f}\n" for f in found)
        + "p\truff check\tsrc/p/gain.pyi E999\n"
    )
    with pytest.raises(AssertionError, match="only shrinks"):
        G.check("ex", root)


def test_a_line_about_a_file_this_run_lacks_is_not_gone(
    root: Path, tmp_path_factory, monkeypatch
) -> None:
    """kitchen_sink's `tone` exists only where doppler does: its lines are
    not applicable elsewhere, not fixed."""
    ratchets = tmp_path_factory.mktemp("gate_a")
    monkeypatch.setattr(G, "RATCHET_DIR", ratchets)
    monkeypatch.setattr(G, "UPDATE", False)
    monkeypatch.setattr(G, "SHRINK", True)
    found = G.findings(root)
    elsewhere = [
        "p\truff check\tsrc/p/tests/test_tone.py F401",
        "absent_project\tstatus --check\tSTALE",
    ]
    (ratchets / "ex.txt").write_text(
        "".join(f"{f}\n" for f in [*found, *elsewhere])
    )
    G.check("ex", root)  # neither line could be observed here
