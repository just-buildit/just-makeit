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
GATE: a run whose pytest colours its output is read like one that does not
      (gh-1845), whatever turned the colour on.
GATE: a collection error is refused when pytest goes on past it and names a
      FAILED test beside it -- under xdist, as ``make test`` runs, and
      ``--continue-on-collection-errors`` (gh-1933).
GATE: the run is read the way pytest wrote it (gh-1945): a red run of only
      unittest subtests (``SUBFAILED``) is accepted and names its test; a
      run whose output could hide a collection error is refused; what a
      test PRINTS is not read as pytest's own line; a node id may hold
      whitespace.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
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


def _run(proj: Path, anchor: str, replacement: str, cmd=None):
    target = proj / "mod.py"
    before = target.read_bytes()
    try:
        return sab.sabotage(
            target, anchor, replacement, cmd or _cmd(), root=proj
        )
    finally:
        assert target.read_bytes() == before, "not restored byte-identical"


def test_a_sabotage_that_lands_and_goes_red_names_the_failure(proj):
    failed = _run(proj, "    return 1\n", "    return 0\n")
    assert failed == ["test_mod.py::test_one"]


def _sabotaged_output(proj: Path, cmd, anchor: str, repl: str) -> str:
    """The run *cmd* makes with the sabotage in place, measured without the
    helper -- to show a case reaches the output shape it is about."""
    target = proj / "mod.py"
    before = target.read_bytes()
    target.write_text(
        before.decode("utf-8").replace(anchor, repl), encoding="utf-8"
    )
    sab._clear_pycache(proj)
    try:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
    finally:
        target.write_bytes(before)
        sab._clear_pycache(proj)
    return sab._SGR.sub("", r.stdout + r.stderr)


#: How a unittest subtest describes itself, and so how its ``SUBFAILED``
#: word reads: ``(i=0)``, ``[msg]``, ``[msg] (i=0)`` -- the message here
#: holding a ``)`` -- and ``(<subtest>)`` with neither.
_SUBTESTS = {
    "kwargs": "self.subTest(i=i)",
    "msg": "self.subTest('case')",
    "msg-and-kwargs": "self.subTest('a) b', i=i)",
    "bare": "self.subTest()",
}


@pytest.mark.parametrize("spelling", list(_SUBTESTS))
def test_a_red_run_of_only_subtests_names_their_test(
    proj, spelling, monkeypatch
):
    """gh-1945: pytest 9 reports a failing unittest ``self.subTest`` only
    as ``SUBFAILED(i=0) test_sub.py::T::test_sub``, with no FAILED line
    for the test, and the helper refused that red run as naming no FAILED
    test. It names the TEST, once, however many of its subtests failed."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (proj / "test_sub.py").write_text(
        "import unittest\n\nfrom mod import one\n\n\n"
        "class T(unittest.TestCase):\n    def test_sub(self):\n"
        "        for i in range(2):\n"
        f"            with {_SUBTESTS[spelling]}:\n"
        "                self.assertEqual(one(), 1)\n",
        encoding="utf-8",
    )
    cmd = _cmd()[:-1] + ["test_sub.py"]
    failed = _run(proj, "    return 1\n", "    return 0\n", cmd)
    assert failed == ["test_sub.py::T::test_sub"]
    # Arm the case: the run names its failures in SUBFAILED lines and in
    # nothing else. pytest 8 has no SUBFAILED -- it reports this failure as
    # FAILED, the shape the helper always read -- so there this case runs
    # on the old shape, and it is armed wherever pytest 9 makes the new one.
    out = _sabotaged_output(proj, cmd, "    return 1\n", "    return 0\n")
    if int(pytest.__version__.split(".")[0]) >= 9:
        assert re.search(r"^SUBFAILED", out, re.M), out
        assert not re.search(r"^FAILED ", out, re.M), out


def test_a_failed_id_holding_whitespace_is_named_whole(proj):
    """gh-1945: the id was read as ``\\S+``, so ``test_w[a b]`` was named
    ``test_w[a`` and ``test_w[c - d]`` was named ``test_w[c`` -- not the
    test the caller checks for. A parametrize id may hold `` - `` too, the
    separator pytest puts before its message."""
    (proj / "test_ws.py").write_text(
        "import pytest\n\nfrom mod import one\n\n\n"
        "@pytest.mark.parametrize('v', ['a b', 'c - d'])\n"
        "def test_w(v):\n    assert one() == 1\n",
        encoding="utf-8",
    )
    cmd = _cmd()[:-1] + ["test_ws.py"]
    failed = _run(proj, "    return 1\n", "    return 0\n", cmd)
    assert failed == ["test_ws.py::test_w[a b]", "test_ws.py::test_w[c - d]"]


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


#: A gate that reads the module's SOURCE, beside the test that imports it --
#: the shape of many of jm's own gates. One sabotage that breaks the
#: module's syntax fails this by assertion and errors ``test_mod.py`` in
#: collection, in the same run: gh-1933's repro.
_SRC_TEST = (
    "from pathlib import Path\n\n\n"
    "def test_one_is_spelled():\n"
    "    src = Path(__file__).with_name('mod.py').read_text('utf-8')\n"
    "    assert 'def one():' in src\n"
)

#: Each way pytest goes ON past a module that did not collect, so the run
#: names a FAILED test beside it and prints no ``Interrupted:`` (pytest 9.1,
#: xdist 3.8, measured). ``make test`` runs ``-n auto``; ``-n 2`` is the
#: same shape on fewer cores. ``--tb=no`` leaves only the summary's
#: ``ERROR test_mod.py`` line and ``-rf`` only the ``ERROR collecting``
#: header, so each of the two readings has a case no other one covers.
_GOES_ON = {
    "xdist": ["-n", "2"],
    "continue-on-collection-errors": ["--continue-on-collection-errors"],
    "xdist-summary-line-only": ["-n", "2", "--tb=no"],
    "xdist-header-only": ["-n", "2", "-rf"],
}


@pytest.mark.parametrize("mode", list(_GOES_ON))
def test_a_collection_error_beside_a_failure_is_refused(
    proj, mode, monkeypatch
):
    """gh-1933: under xdist and ``--continue-on-collection-errors`` a module
    that fails to import is one column-0 ``ERROR test_mod.py`` line and a
    section header, beside a FAILED test the run went on to name. The helper
    read neither and accepted the sabotage, though the module's own tests
    never ran."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (proj / "test_src.py").write_text(_SRC_TEST, encoding="utf-8")
    cmd = _cmd() + _GOES_ON[mode] + ["test_src.py"]
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(proj, "def one():", "def one(:", cmd)
    # Arm the case: with the collection reading switched off, the same
    # sabotage is ACCEPTED, naming the source gate. So the run really went
    # on past the error (an interrupted one names no FAILED test), and it is
    # the collection reading, nothing else, that refuses it.
    monkeypatch.setattr(sab, "_uncollected", lambda out, results: False)
    failed = _run(proj, "def one():", "def one(:", cmd)
    assert failed == ["test_src.py::test_one_is_spelled"]


def test_a_collection_error_under_a_spaced_path_is_refused(proj, monkeypatch):
    """gh-1945: the summary line's id was read as ``\\S+``, so a module
    under ``my tests/`` -- ``ERROR my tests/test_imp.py - ...`` -- was not
    seen to fail collection; under ``--tb=no`` that line is all the run
    says of it, and the sabotage was accepted. The root ``conftest.py`` puts
    the project on ``sys.path`` for the module under the spaced directory."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (proj / "conftest.py").write_text("", encoding="utf-8")
    (proj / "test_src.py").write_text(_SRC_TEST, encoding="utf-8")
    (proj / "my tests").mkdir()
    (proj / "my tests" / "test_imp.py").write_text(
        _TEST.replace("test_one", "test_imp"), encoding="utf-8"
    )
    cmd = _cmd()[:-1] + _GOES_ON["xdist-summary-line-only"]
    cmd += ["test_src.py", "my tests/test_imp.py"]
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(proj, "def one():", "def one(:", cmd)
    # Armed as above: the run went on past the module to name the source
    # gate, and only the collection reading refuses it.
    monkeypatch.setattr(sab, "_uncollected", lambda out, results: False)
    failed = _run(proj, "def one():", "def one(:", cmd)
    assert failed == ["test_src.py::test_one_is_spelled"]


#: Each way a run's output can hide a module that failed to collect, beside
#: a FAILED test it went on to name (gh-1945): no tracebacks (``--tb=no``)
#: and an ``-r`` without ``E`` leave the count line, ``1 failed, 1 error``
#: -- the count a fixture that raised gives too -- and ``-qq`` (``_cmd``'s
#: ``-q`` and one more) drops even that.
_HIDES = {
    "xdist-count-line-only": ["-n", "2", "-rf", "--tb=no"],
    "continue-count-line-only": [
        "--continue-on-collection-errors",
        "-rf",
        "--tb=no",
    ],
    "xdist-nothing": ["-q", "-n", "2", "-rf", "--tb=no"],
}


@pytest.mark.parametrize("mode", list(_HIDES))
def test_a_run_that_could_hide_a_collection_error_is_refused(
    proj, mode, monkeypatch
):
    """Refused rather than read as clean: "no error shown" is not "no
    error" when the run could not have shown one. The helper accepted each
    of these, though ``test_mod.py`` never imported."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (proj / "test_src.py").write_text(_SRC_TEST, encoding="utf-8")
    cmd = _cmd() + _HIDES[mode] + ["test_src.py"]
    with pytest.raises(sab.Refused, match="could HIDE"):
        _run(proj, "def one():", "def one(:", cmd)
    # Arm the case: with only this reading switched off, the same sabotage
    # is ACCEPTED -- the run went on past the module, named the source
    # gate, and printed nothing the collection reading sees.
    monkeypatch.setattr(sab, "_hidden_errors", lambda *a: None)
    failed = _run(proj, "def one():", "def one(:", cmd)
    assert failed == ["test_src.py::test_one_is_spelled"]


#: Runs that show they had no error, though one of the two ways is off:
#: the count line says none (``--tb=no``, ``-r`` without ``E``), or the
#: tracebacks are printed (``-qq``, no count line). Refusing these is the
#: over-wide fix -- every ``--tb=no`` run, or every ``-qq`` one.
_SHOWS_NONE = {
    "count-line-says-none": ["-rf", "--tb=no"],
    "tracebacks-under-qq": ["-q", "-rf"],
}


@pytest.mark.parametrize("mode", list(_SHOWS_NONE))
def test_a_run_that_shows_it_had_no_error_is_accepted(proj, mode, monkeypatch):
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    cmd = _cmd() + _SHOWS_NONE[mode]
    failed = _run(proj, "    return 1\n", "    return 0\n", cmd)
    assert failed == ["test_mod.py::test_one"]


_FIXTURE_TEST = (
    "import pytest\n\nfrom mod import untested\n\n\n"
    "@pytest.fixture\ndef two():\n    return untested()\n\n\n"
    "def test_two(two):\n    assert two == 2\n"
)

#: Column-0 ``ERROR `` lines that are NOT a module failing to collect, each
#: printed beside ``test_mod.py::test_one`` failing for real, as
#: (test file, its text, extra argv, the tests the helper must name). A
#: sabotage that goes red like this went red for the right reason; refusing
#: it is the false refusal column-0 anchoring was introduced to stop.
_NOT_COLLECTION = {
    # A fixture that raised: the summary line's id names a TEST, ``::``
    # and all -- ``ERROR test_fix.py::test_two - RuntimeError: sabotaged``.
    "fixture-error": (
        "test_fix.py",
        _FIXTURE_TEST,
        [],
        ["test_mod.py::test_one"],
    ),
    # The same under ``--tb=no``: that summary line, and a count line that
    # says ``1 error``, are all the run says of it -- and they suffice, as
    # the line names a test (gh-1945 refuses an error it does not name).
    "fixture-error-tb-no": (
        "test_fix.py",
        _FIXTURE_TEST,
        ["--tb=no"],
        ["test_mod.py::test_one"],
    ),
    # A captured log record, in pytest's default log format:
    # ``ERROR    gate:test_log.py:7 mod.py broke``.
    "captured-log": (
        "test_log.py",
        "import logging\n\nfrom mod import one\n\n\n"
        "def test_logged():\n"
        "    logging.getLogger('gate').error('mod.py broke')\n"
        "    assert one() == 1\n",
        [],
        ["test_mod.py::test_one", "test_log.py::test_logged"],
    ),
    # A test that PRINTS a module's summary line, and a test's, into its
    # captured stdout at column 0 (gh-1945): the first was refused as a
    # COLLECTION error, and the second would name a test that never ran.
    "captured-stdout": (
        "test_print.py",
        "from mod import one\n\n\ndef test_printed():\n"
        "    print('ERROR x.py')\n    print('FAILED x.py::test_never')\n"
        "    assert one() == 1\n",
        [],
        ["test_mod.py::test_one", "test_print.py::test_printed"],
    ),
}


@pytest.mark.parametrize("case", list(_NOT_COLLECTION))
def test_an_error_line_that_is_not_a_module_is_accepted(
    proj, case, monkeypatch
):
    """gh-1933 reads ``ERROR <id>`` as a collection error only for an id
    with no ``::``, and gh-1945 only below the short summary's rule, where
    pytest prints nothing but its own lines. Each case here is a column-0
    ``ERROR`` line one of those excludes; without it, the sabotage is
    refused as a COLLECTION error that never happened."""
    name, text, argv, expected = _NOT_COLLECTION[case]
    (proj / name).write_text(text, encoding="utf-8")
    args = (
        proj,
        "    return 1\n\n\ndef untested():\n    return 2\n",
        "    return 0\n\n\ndef untested():\n"
        "    raise RuntimeError('sabotaged')\n",
        _cmd() + argv + [name],
    )
    assert sorted(_run(*args)) == sorted(expected)
    # Arm the case: read EVERY column-0 ``ERROR `` as a collection error and
    # the same sabotage is refused -- so the run does print the line the
    # real reading had to tell apart from a module.
    monkeypatch.setattr(sab, "_COLLECTION", re.compile(r"^ERROR ", re.M))
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(*args)


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


#: Each way a caller's pytest comes to colour its output, as (environment,
#: extra argv) -- measured against pytest 9.1. FORCE_COLOR is the issue's
#: (gh-1845); PY_COLORS=1 wins over NO_COLOR; ``--color=yes`` wins over every
#: variable, which is why the helper strips the colour rather than trying to
#: turn it off.
_COLOURED = {
    "FORCE_COLOR": ({"FORCE_COLOR": "3"}, []),
    "PY_COLORS": ({"PY_COLORS": "1"}, []),
    "--color=yes": ({}, ["--color=yes"]),
}


@pytest.fixture(params=list(_COLOURED))
def coloured(request, monkeypatch) -> "list[str]":
    """Colour on, by one route, with every other route cleared first -- so a
    developer's own NO_COLOR cannot switch the case off. Returns the argv to
    append to the command."""
    env, argv = _COLOURED[request.param]
    for var in ("FORCE_COLOR", "PY_COLORS", "NO_COLOR", "PYTEST_ADDOPTS"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    return argv


def _assert_colour_arrives(proj: Path, cmd: "list[str]") -> None:
    """Arm the case: this pytest really does colour under this setting.

    A run the colour never reached is read correctly by the unfixed helper
    too, so without this a case could pass on the bug and gate nothing --
    the way gh-1845 hid, since CI sets no colour.
    """
    r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
    assert r.returncode == 0 and "\x1b[" in r.stdout, r.stdout + r.stderr


def test_a_coloured_red_run_still_names_the_failure(proj, coloured):
    """gh-1845: a coloured run's summary reads ``\\x1b[31mFAILED\\x1b[0m
    test_mod.py::\\x1b[1mtest_one``, so ``^FAILED`` matched nothing and the
    helper refused every sabotage that had gone red for the right reason.
    The name must come back clean too: one with escapes left inside it
    would not equal the test the caller is checking for."""
    cmd = _cmd() + coloured
    _assert_colour_arrives(proj, cmd)
    failed = _run(proj, "    return 1\n", "    return 0\n", cmd)
    assert failed == ["test_mod.py::test_one"]


def test_a_coloured_collection_error_is_still_refused(proj, coloured):
    """The other reader of the same output. Under
    ``--continue-on-collection-errors`` the run goes on to name a FAILED
    test, and the one line saying a module never imported is
    ``ImportError while importing test module`` -- coloured, at column 0.
    A fix that let only ``FAILED`` through the colour would ACCEPT this
    sabotage, which proves nothing about the module that did not import."""
    (proj / "test_imp.py").write_text(
        "from mod import untested\n\n\ndef test_untested():\n"
        "    assert untested() == 2\n",
        encoding="utf-8",
    )
    cmd = _cmd() + coloured
    cmd += ["--continue-on-collection-errors", "test_imp.py"]
    _assert_colour_arrives(proj, cmd)
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(
            proj,
            "    return 1\n\n\ndef untested():\n",
            "    return 0\n\n\ndef renamed():\n",
            cmd,
        )


def test_a_coloured_collection_error_under_xdist_is_still_refused(
    proj, coloured
):
    """gh-1933's shape, coloured: under xdist the summary line reads
    ``\\x1b[31mERROR\\x1b[0m test_mod.py - ...`` and the header starts with
    an escape, so both are found only in the colour-blind reading -- the
    combination a developer with FORCE_COLOR set gets from ``make test``."""
    (proj / "test_src.py").write_text(_SRC_TEST, encoding="utf-8")
    cmd = _cmd() + coloured + _GOES_ON["xdist"] + ["test_src.py"]
    _assert_colour_arrives(proj, cmd)
    with pytest.raises(sab.Refused, match="COLLECTION"):
        _run(proj, "def one():", "def one(:", cmd)
