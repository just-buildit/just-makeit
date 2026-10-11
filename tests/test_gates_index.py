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

The floor is derived too (gh-2038): the gates declared at the merge base,
read by the same parse, rather than a committed file that every two gate
PRs conflicted on. These tests ask git, in a repository of their own.

GATE: a gate that declares an obligation keeps declaring it; a branch that
      retires one says so in a `Gate-Retired:` commit trailer, so the
      removal is reviewed.
"""

from __future__ import annotations

import doctest
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import _scratchgit
import pytest
from _scratchgit import Repo

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


def test_the_scratch_repos_doctests_run():
    """`tests/_scratchgit.py` is no test module, so nothing else runs its
    examples -- and they are its claim that a merge it calls clean is."""
    result = doctest.testmod(_scratchgit, verbose=False)
    assert result.attempted and not result.failed, result


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
        # The standard's target since gh-1526, declared at the HAS_CHANGELOG
        # flag in the Makefile that switches it on.
        assert (
            "Makefile:a change under src/ carries a changelog.d/ fragment."
            in declared
        ), "the changelog-entry gate is a make target and must ride too"


def _gate(text: str) -> str:
    return f'"""t.\n\nGATE: {text}\n"""\n'


#: The base every ratchet test branches from: two test gates, one makefile
#: gate, and a test module that declares nothing.
_BASE = {
    "tests/test_kept.py": _gate("keep this."),
    "tests/test_dropped.py": _gate("drop this."),
    "tests/test_plain.py": '"""declares nothing."""\n',
    "Makefile": "# GATE: a make target holds this.\nall:\n\t@true\n",
}


class TestTheRatchet:
    """Against the merge base, in a repository of its own -- never the real
    tree, which an earlier version sabotaged in place and restored with
    `git checkout`, reverting an uncommitted marker."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        # The script's own git reads GIT_*; from inside a hook they would
        # aim it at the outer repository (see `_scratchgit`).
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(key)
        repo = Repo(tmp_path / "r")
        (repo.root / "tests").mkdir()
        for rel, text in _BASE.items():
            (repo.root / rel).write_text(text, encoding="utf-8")
        repo.commit("base")
        assert GI.check(repo.root, "main") == 0
        return repo

    @staticmethod
    def _on(repo, edit, message: str = "") -> None:
        """Commit *edit* on a branch of ``main``, and stay on it."""
        repo.branch("topic", edit, message)
        repo.git("checkout", "-q", "topic")

    @pytest.mark.parametrize(
        "name, edit",
        [
            (
                "test_dropped",
                lambda root: (root / "tests/test_dropped.py").unlink(),
            ),
            (
                "test_dropped",
                lambda root: (root / "tests/test_dropped.py").write_text(
                    _gate("drop this.").replace("GATE:", "XXXX:")
                ),
            ),
            (
                "Makefile:a make target holds this.",
                lambda root: (root / "Makefile").write_text("all:\n"),
            ),
        ],
        ids=["file-deleted", "line-dropped", "makefile-gate"],
    )
    def test_a_dropped_gate_is_refused(self, repo, capsys, name, edit):
        """Committed on the branch, so only the merge base still declares
        it: the check that compared with HEAD would pass this."""
        self._on(repo, edit)
        assert GI.check(repo.root, "main") == 1
        err = capsys.readouterr().err
        assert f"  {name}  (declared in {GI._where(name)})" in err, err
        assert f"Gate-Retired: {name}" in err, "the remedy, ready to paste"

    def test_an_uncommitted_drop_is_refused(self, repo):
        (repo.root / "tests/test_dropped.py").unlink()
        assert GI.check(repo.root, "main") == 1

    def test_a_retired_gate_passes(self, repo):
        """The one way a gate goes: said in a commit, where it is reviewed."""
        self._on(
            repo,
            lambda root: (root / "tests/test_dropped.py").unlink(),
            "chore: retire it\n\nGate-Retired: test_dropped\n",
        )
        assert GI.check(repo.root, "main") == 0

    def test_a_new_gate_passes_with_no_other_file_touched(self, repo):
        """Nothing to record: the old floor wanted a line in a shared file
        too, and that line is what every two gate PRs conflicted on."""
        self._on(
            repo,
            lambda root: (root / "tests/test_new.py").write_text(
                _gate("do it.")
            ),
        )
        assert GI.check(repo.root, "main") == 0
        touched = repo.git("diff", "--name-only", "main", "HEAD").stdout
        assert touched.split() == ["tests/test_new.py"]

    def test_adjacent_gates_merge_in_either_order(self, repo):
        """gh-2038: two branches each adding a gate that sorts beside the
        other's merge with nothing to resolve, and the merge passes."""
        for name in ("test_kept_a", "test_kept_b"):
            repo.branch(
                name,
                lambda root, n=name: (root / f"tests/{n}.py").write_text(
                    _gate(f"{n} holds.")
                ),
            )
        for order in (
            ("test_kept_a", "test_kept_b"),
            ("test_kept_b", "test_kept_a"),
        ):
            assert repo.conflicts(*order) == [], order
            repo.git("checkout", "-q", Repo.MERGED)
            assert GI.check(repo.root, "main") == 0
            repo.git("checkout", "-q", "main")


@pytest.mark.parametrize("argv", [[], ["--check", "--base", "HEAD"]])
def test_the_make_targets_run(argv):
    """`make lint` depends on `--check`, so both must work as invoked.

    ``--base HEAD`` because the real base is the merge base with
    ``origin/main``, which `make lint` passes and a clone need not have;
    `TestTheRatchet` holds the comparison itself, in a repository of its
    own."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip()
