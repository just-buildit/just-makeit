"""The index of gate obligations is derived, and cannot quietly shrink.

`make gates-index` prints, at session start, the obligations this repo's
own gates enforce. It exists because a gate teaches at the moment it fires
and by then the work is done: five times in one session a change was
correct and its surrounding contract was not -- a `src/` edit with no
CHANGELOG entry, a new module with no row in CLAUDE.md's table, a tagged
release with no changelog section, a test driving the CLI through a child,
and a `_createonly` rule no fixture could reach.

The index is DERIVED from the gates. A hand-written catalogue would be a
note about notes, stale the first time a gate is renamed.

**Which files may declare an obligation is not derivable, and that was
measured rather than assumed.** Selecting "gates that scan the repo" picks
109 files when the tell is a bare `.glob(` and 14 when it is a module-level
ROOT constant -- and those 14 miss four of the five above. Over-selection
makes the index noise; under-selection drops a rule silently, which is the
one failure a turn-zero index cannot have. So a human declares, and a
ratchet refuses shrinkage.

This file is that ratchet's test, and it carries its own declaration --
if the mechanism cannot describe itself it is not worth trusting.

GATE: a gate that declares an obligation keeps declaring it; record a
      removal with `make gates-index-update` so it is reviewed.
"""

from __future__ import annotations

import doctest
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gates-index.py"


def _module():
    """Import the script by path -- it is a script, not a package member."""
    spec = importlib.util.spec_from_file_location("gates_index", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GI = _module()


def test_the_scripts_own_doctests_run():
    """`testpaths = ["tests"]`, so nothing else would ever run them.

    A derivation whose examples are never executed is a comment. This is
    the execution home for them (`make lint` runs the ratchet; `make test`
    runs this).
    """
    result = doctest.testmod(GI, verbose=False)
    assert result.failed == 0, f"{result.failed} doctest(s) failed in {SCRIPT}"


class TestTheIndexIsReal:
    def test_it_lists_something(self):
        found = GI.collect(ROOT / "tests")
        assert found, "no gate declares an obligation, so the index is empty"

    def test_every_declared_gate_still_exists(self):
        """A floor entry naming a deleted file would never fail otherwise."""
        for name in GI.declared_gates(ROOT / "tests"):
            if ":" in name:  # a makefile gate, keyed by its text
                where = name.split(":", 1)[0]
                assert (ROOT / where).exists(), f"{where} is gone"
            else:
                assert (ROOT / "tests" / f"{name}.py").exists(), name

    def test_the_gates_that_caught_real_omissions_are_declared(self):
        """Named explicitly, because these five are the whole reason.

        A generic "something is declared" assertion would still pass with
        the useful entries gone -- which is exactly how the measured
        heuristic failed, and the trap this file is about.
        """
        declared = set(GI.declared_gates(ROOT / "tests"))
        for name in (
            "test_changelog_has_every_release",
            "test_claude_md_drift",
            "test_gh1374_cli_in_process",
            "test_gh949_outdated",
        ):
            assert name in declared, f"{name} stopped declaring its rule"
        assert any(n.startswith("local.mk:") for n in declared), (
            "the changelog-entry gate is a make target and must ride too"
        )


class TestTheRatchet:
    def test_a_dropped_declaration_is_refused(self, tmp_path):
        """Sabotage, in a COPY -- never the real tree.

        An earlier version of this check sabotaged in place and restored
        with `git checkout`, which reverted an uncommitted marker and made
        the next run measure the wrong thing.
        """
        assert GI.check(ROOT) == 0, "the repo is not clean to begin with"

        gate = ROOT / "tests" / "test_claude_md_drift.py"
        text = gate.read_text(encoding="utf-8")
        assert "GATE:" in text
        gate_broken = text.replace("GATE:", "XXXX:", 1)

        fake = tmp_path / "repo"
        (fake / "tests").mkdir(parents=True)
        (fake / "tests" / "test_claude_md_drift.py").write_text(gate_broken)
        (fake / "tests" / "gates-declared.txt").write_text(
            "test_claude_md_drift\n"
        )

        assert GI.check(fake) == 1

    def test_a_new_gate_is_free(self, tmp_path):
        """Adding one must not need a second commit to the floor."""
        fake = tmp_path / "repo"
        (fake / "tests").mkdir(parents=True)
        (fake / "tests" / "test_new.py").write_text(
            '"""t.\n\nGATE: do it.\n"""\n'
        )
        (fake / "tests" / "gates-declared.txt").write_text("")
        assert GI.check(fake) == 0


@pytest.mark.parametrize("flag", ["", "--check"])
def test_the_make_targets_run(flag):
    """`make lint` depends on `--check`, so both must work as invoked."""
    argv = [sys.executable, str(SCRIPT)] + ([flag] if flag else [])
    r = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip()
