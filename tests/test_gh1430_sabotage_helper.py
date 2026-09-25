"""gh-1430: the sabotage helper refuses a sabotage that proves nothing.

`scripts/sabotage.py` is the one way this repo proves a gate. Each of its
refusals is one way a hand sabotage failed silently and was read as a
result (the issue's three instances, and gh-1583's anchor a formatter had
rewrapped). Every case runs against a throwaway project with a real pytest,
because the helper's claim is about what a real run reports -- and after
every case, refused or not, the file must be back byte for byte.

GATE: a sabotage is accepted only if its anchor occurs once, the edit lands,
      the command goes from green to red naming a FAILED test without a
      collection error, and the file is restored byte-identical.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sabotage", Path(__file__).parent.parent / "scripts" / "sabotage.py"
)
sab = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sab)

_MOD = "def one():\n    return 1\n\n\ndef untested():\n    return 2\n"
_TEST = "from mod import one\n\n\ndef test_one():\n    assert one() == 1\n"


@pytest.fixture
def proj(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "mod.py").write_text(_MOD, encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(_TEST, encoding="utf-8")
    return tmp_path


def _cmd():
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "test_mod.py"]  # fmt: skip


def _run(proj: Path, anchor: str, replacement: str):
    target = proj / "mod.py"
    before = target.read_bytes()
    try:
        return sab.sabotage(target, anchor, replacement, _cmd(), root=proj)
    finally:
        assert target.read_bytes() == before, "not restored byte-identical"


def test_a_sabotage_that_lands_and_goes_red_names_the_failure(proj):
    failed = _run(proj, "    return 1\n", "    return 0\n")
    assert failed == ["test_mod.py::test_one"]


@pytest.mark.parametrize(
    "anchor, why",
    [("    return 7\n", "0 times"), ("return", "2 times")],
    ids=["absent", "ambiguous"],
)
def test_an_anchor_that_is_not_there_exactly_once_is_refused(
    proj, anchor, why
):
    with pytest.raises(sab.Refused, match=why):
        _run(proj, anchor, "    return 0\n")


def test_a_replacement_equal_to_the_anchor_is_refused(proj):
    with pytest.raises(sab.Refused, match="nothing changed"):
        _run(proj, "    return 1\n", "    return 1\n")


def test_a_sabotage_the_gate_does_not_see_is_refused(proj):
    with pytest.raises(sab.Refused, match="stayed GREEN"):
        _run(proj, "    return 2\n", "    return 3\n")


def test_a_sabotage_that_breaks_collection_is_refused(proj):
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(proj, "def one():", "def one(:")


def test_a_command_red_before_the_sabotage_is_refused(proj):
    (proj / "test_mod.py").write_text(
        _TEST.replace("== 1", "== 5"), encoding="utf-8"
    )
    with pytest.raises(sab.Refused, match="BEFORE"):
        _run(proj, "    return 1\n", "    return 0\n")


def test_a_red_that_names_no_failed_test_is_refused(proj):
    """Red is not a test result: a command that dies without naming a failed
    test (a crash, a non-pytest check) proves nothing about an assertion.
    Found by running the helper on itself -- no case covered it."""
    target = proj / "mod.py"
    before = target.read_bytes()
    cmd = [sys.executable, "-c", "import mod; assert mod.one() == 1"]
    with pytest.raises(sab.Refused, match="named no FAILED"):
        try:
            sab.sabotage(target, "    return 1\n", "    return 0\n", cmd, proj)
        finally:
            assert target.read_bytes() == before
