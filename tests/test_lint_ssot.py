"""The lint toolchain has one source of truth per concern — and stays that way.

Three files each own exactly one thing:

- ``pyproject.toml`` (``[dependency-groups] dev``) — WHICH tools, WHAT versions,
  locked by ``uv.lock``.
- ``Makefile`` — HOW a tool is invoked (binary + flags + paths), as
  configuration consumed by the vendored ``standard.mk``, which owns the
  targets themselves.
- ``.pre-commit-config.yaml`` — WHEN a check runs (which paths trigger it).

Every caller routes through a Makefile target, so a human running ``make
format``, a git hook, and the CI gate cannot disagree. Both halves of that
contract had already broken in practice:

- an unpinned ``uvx ruff`` (documented in CLAUDE.md) resolved to a newer ruff
  than the pinned one and silently reformatted 15 unrelated files;
- mdformat's plugins were unpinned pre-commit ``additional_dependencies``, so
  the hook env drifted to ``mdformat-mkdocs 5.1.4`` while the config still
  read as if it were pinned;
- and nothing in CI ran the hooks at all, so a genuinely broken markdown file
  sat on ``main`` from #469 until it was found by accident.

These tests assert the invariants rather than any one command string, so they
keep holding as targets get added.
"""

import functools
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
MAKEFILE = ROOT / "Makefile"
STANDARD = ROOT / "standard.mk"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# Tools whose version is pinned in pyproject's dev group; they must be invoked
# through the Makefile so the pinned version is the one that actually runs.
MANAGED_TOOLS = ("ruff", "mdformat")


def _read(p):
    return p.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _make_db():
    """Every target make knows about, from its own database.

    ``-p`` dumps the parsed database and ``-n`` keeps it from running
    anything; ``-r`` drops the built-in rules, leaving only what this repo
    defines. This sees targets that no file spells out literally — the
    ``lint-<tool>`` rules standard.mk generates with ``$(eval)`` — which is
    why the tests below ask make instead of grepping the Makefile.
    """
    proc = subprocess.run(
        ["make", "-rpn", "--no-print-directory"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # A makefile that fails to parse yields an empty database, and every test
    # below then fails with "this target does not exist" — true, but it names
    # the symptom rather than the cause. That cost a red macOS matrix to read:
    # the real message was standard.mk rejecting the GNU make 3.81 that macOS
    # ships, and it was nowhere in the pytest output.
    assert proc.returncode == 0, (
        "`make -rpn` failed, so there is no database to check against:\n"
        + (proc.stderr or "(no stderr)")
    )
    return proc.stdout


def _hooks():
    """Every ``(id, entry)`` pair in .pre-commit-config.yaml.

    Walked by hand rather than with PyYAML: the unit suite runs in a minimal
    ``uv run --no-project`` env (see the Makefile's PYTEST) that deliberately
    carries only pytest + numpy, so importing yaml here would make this file
    the one test that needs a dependency nothing else needs.

    Returns every hook, not the first match per tool — an earlier version of
    this test regex-matched only the first `- id: ruff*` block and therefore
    passed while `ruff-format` had been switched to a raw `uvx` command.
    """
    pairs, current = [], None
    for raw in _read(PRECOMMIT).splitlines():
        line = raw.strip()
        if line.startswith("- id:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("entry:") and current is not None:
            pairs.append((current, line.split(":", 1)[1].strip()))
            current = None
    return pairs


class TestMakefileOwnsInvocation:
    def test_every_managed_hook_dispatches_to_make(self):
        """EVERY hook for a managed tool calls make — not just the first."""
        hooks = _hooks()
        assert hooks, "parsed no hooks out of .pre-commit-config.yaml"
        checked = []
        for hook_id, entry in hooks:
            tool = hook_id.split("-")[0]
            if tool not in MANAGED_TOOLS:
                continue
            checked.append(hook_id)
            assert entry.startswith("make "), (
                f"hook {hook_id!r} must dispatch through a make target so it "
                f"cannot drift from `make format`; got: {entry!r}"
            )
        for tool in MANAGED_TOOLS:
            assert any(h.split("-")[0] == tool for h in checked), (
                f"no hook found for {tool} — did it get renamed?"
            )

    def test_no_hook_pins_a_managed_tool_version(self):
        """Versions live in pyproject only — no second copy to forget."""
        cfg = _read(PRECOMMIT)
        for tool in MANAGED_TOOLS:
            assert not re.search(rf"{tool}[a-z-]*==", cfg), (
                f"{tool} version is pinned in .pre-commit-config.yaml; it "
                "belongs in pyproject.toml's dev group (uv.lock pins it)"
            )

    def test_managed_tools_pinned_in_dev_group(self):
        """...and they really are pinned there, exactly."""
        pyproject = _read(ROOT / "pyproject.toml")
        dev = pyproject[pyproject.index("[dependency-groups]") :]
        for tool in MANAGED_TOOLS:
            assert re.search(rf'"{tool}==[\d.]+', dev), (
                f"{tool} must be pinned with == in the dev group so every "
                "machine and CI format identically"
            )

    def test_makefile_defines_the_tool_variables(self):
        mk = _read(MAKEFILE)
        for var in ("RUFF", "MDFORMAT", "PRE_COMMIT"):
            assert re.search(rf"^{var}\s*=", mk, re.M), (
                f"{var} must be defined in the Makefile's tooling block"
            )

    @pytest.mark.parametrize(
        "target", ["format", "lint", "lint-ruff", "lint-mdformat"]
    )
    def test_entry_point_targets_exist(self, target):
        # Asked of make rather than grepped out of a file: the shared targets
        # live in the vendored standard.mk, and the `lint-<tool>` dispatch
        # rules are stamped out by `$(eval)`, so they appear as literal text
        # in no file at all. What callers depend on is that `make <target>`
        # works, which is exactly what the database answers.
        assert re.search(rf"^{re.escape(target)}:", _make_db(), re.M), (
            f"`make {target}` is referenced by CLAUDE.md, the hooks, or CI"
        )


class TestTheStandardStaysTheStandard:
    """The cross-org Makefile standard, as invariants rather than as prose.

    Shared targets live in the vendored ``standard.mk`` (canonical:
    <https://just-buildit.github.io/standard.mk>); per-repo variation is the
    configuration in ``Makefile``. Plan and success criteria: the
    just-buildit/.github README, "Makefile standard".
    """

    def test_standard_mk_is_vendored(self):
        assert STANDARD.exists(), (
            "standard.mk is missing — the Makefile includes it, and the "
            "shared targets live there"
        )

    def test_makefile_defines_no_shared_targets(self):
        """Criterion 1: the Makefile is configuration, not implementation.

        A target defined here is a target that has stopped being shared: the
        drift gate cannot see it, so the next repo to want it copies it, and
        the two copies start diverging. That is the whole failure mode.
        """
        # `:(?!:?=)` so an assignment written without a space before the
        # operator is not read as a rule: `FOO:= bar` and `FOO::= bar` are
        # variables, while `target:` and doppler's `target::` are rules. The
        # lookahead has to cover the whole operator — `::?(?!=)` backtracks to
        # one colon, sees the second, and matches `FOO::=` anyway.
        offenders = [
            f"Makefile:{i}: {line}"
            for i, line in enumerate(_read(MAKEFILE).splitlines(), 1)
            if re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*:(?!:?=)", line)
        ]
        assert not offenders, (
            "these define targets in the Makefile; shared ones belong in "
            "standard.mk, and genuinely local ones in local.mk (named in "
            "LOCAL_TARGETS):\n" + "\n".join(offenders)
        )

    @pytest.mark.parametrize(
        "gate", ["standard-check", "help-check", "ghost-check"]
    )
    def test_lint_runs_the_gate(self, gate):
        """The gates must hang off `lint`, which is what CI runs.

        Criteria 2, 3 and 8 are enforced by gates rather than by review
        precisely because review did not catch the drift that motivated them.
        A gate nothing invokes is back to being review.
        """
        m = re.search(r"^lint:(.*)$", _make_db(), re.M)
        assert m, "no `lint` target in the make database"
        assert gate in m.group(1).split(), (
            f"`make lint` does not run {gate}; CI runs `make lint` and "
            "nothing else, so a gate outside it never runs on a PR"
        )


class TestTheDocsCannotDriftFromTheMakefile:
    """Every `make <target>` this repo's own docs name must exist.

    The contributor docs used to tell a newcomer to run ``uv sync`` and ``uv
    run pytest`` directly. Neither matches what the Makefile does — ``make
    setup`` also syncs the ``dev`` group and installs the git hook, and ``make
    test`` runs pytest in a deliberately isolated ``--no-project`` environment
    so the suite exercises the installed-package path. Following the docs got
    you a different environment than CI, silently: the exact drift the Makefile
    standard exists to close, reintroduced through prose.

    Prose cannot be gated, but the target *names* in it can, so a rename can no
    longer leave the docs quietly wrong.
    """

    # Only this repo's own contributor docs. The user-facing docs describe
    # GENERATED projects, whose Makefile is just-makeit's product and has its
    # own, different target set.
    OWN_DOCS = ("docs/developers/START_HERE.md", "CLAUDE.md")

    @staticmethod
    def _referenced(text):
        """`make <target>` in backticks, or as a command line in a fence.

        Deliberately not a bare-prose match: "make changes" and "standard make
        targets" are English, not commands, and a checker that flags them gets
        switched off.
        """
        found = set(re.findall(r"`make ([a-z][a-z0-9-]*)[^`]*`", text))
        for line in text.splitlines():
            m = re.match(r"^make ([a-z][a-z0-9-]*)", line.strip())
            if m:
                found.add(m.group(1))
        return found

    @pytest.mark.parametrize("doc", OWN_DOCS)
    def test_every_documented_target_exists(self, doc):
        db = _make_db()
        missing = sorted(
            t
            for t in self._referenced(_read(ROOT / doc))
            if not re.search(rf"^{re.escape(t)}:", db, re.M)
        )
        assert not missing, (
            f"{doc} names `make <target>` that does not exist: "
            f"{', '.join(missing)} — rename in the docs, or the target is gone"
        )

    def test_setup_is_the_documented_entry_point(self):
        """Not `uv sync`, which skips the dev group and the git hook."""
        text = _read(ROOT / "docs/developers/START_HERE.md")
        assert "make setup" in text, (
            "START_HERE must point newcomers at `make setup`; plain `uv sync` "
            "leaves them without the dev tools and without the git hook"
        )
        setup_at = text.index("## Development setup")
        section = text[setup_at : setup_at + 1200]
        assert not re.search(r"^uv sync\s*$", section, re.M), (
            "START_HERE's setup section tells contributors to run `uv sync` "
            "directly, which is not what `make setup` does"
        )


class TestNobodyBypassesTheMakefile:
    def test_no_uvx_linter_in_workflows_or_docs(self):
        """`uvx ruff` is the exact footgun that caused the 15-file churn."""
        offenders = []
        targets = list((ROOT / ".github" / "workflows").glob("*.yml"))
        targets += [ROOT / "CLAUDE.md", MAKEFILE, PRECOMMIT]
        for path in targets:
            for i, line in enumerate(_read(path).splitlines(), 1):
                stripped = line.strip()
                # Prose warning ABOUT uvx is fine; an invocation is not.
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                for tool in MANAGED_TOOLS:
                    if re.search(rf"\buvx\b[^\n]*\b{tool}\b", line):
                        offenders.append(f"{path.name}:{i}: {stripped}")
        assert not offenders, (
            "these invoke a pinned tool through uvx, which resolves to "
            "whatever released today — use a make target:\n"
            + "\n".join(offenders)
        )

    def test_ci_lints_via_make(self):
        ci = _read(CI)
        assert re.search(r"^\s*run: make lint\s*$", ci, re.M), (
            "the CI lint job must run the same `make lint` a developer runs"
        )


class TestCIActuallyGates:
    """A lint job that the merge gate ignores is decoration."""

    def test_lint_job_exists(self):
        assert re.search(r"^  lint:$", _read(CI), re.M), (
            "no lint job in ci.yml"
        )

    def test_ci_passed_requires_lint(self):
        ci = _read(CI)
        m = re.search(r"^\s*needs: \[(changes,[^\]]*)\]", ci, re.M)
        assert m, "could not find the ci-passed aggregator's needs list"
        assert "lint" in [n.strip() for n in m.group(1).split(",")], (
            "`lint` must be in the CI-passed aggregator's needs, or the "
            "branch ruleset will merge PRs that fail lint"
        )

    def test_gates_covers_every_make_gate_ci_runs(self):
        """`make gates` must BE the set CI requires, or it is a lie.

        It read `lint docs-check test-all`, which omitted `coverage-gate` --
        the one gate that blocks a merge on a number -- while including
        `docs-check`, which no CI job runs under that name. Wrong in both
        directions, and nothing invoked it, so nothing could notice.

        Asserted mechanically rather than as a literal list, so it keeps
        holding as jobs are added: every make target a CI step runs must be
        reachable from `gates`. Provisioning steps are excluded by name, not
        by pattern, so adding one is a deliberate edit here.

        The converse is deliberately NOT asserted -- `gates` may be a strict
        superset. `docs-check` is the standing example: docs.yml runs the same
        strict build, but only `CI passed` is a required check, so a broken
        docs build cannot block a merge and running it locally is the only
        place it gets caught.
        """
        provisioning = {"install-deps"}
        ci_targets = {
            m.group(1)
            for m in re.finditer(
                r"^\s*(?:- )?run: make (\S+)\s*$", _read(CI), re.M
            )
        } - provisioning
        assert ci_targets, "no `run: make <target>` steps found in ci.yml"

        db = _make_db()

        def prereqs(name):
            m = re.search(rf"^{re.escape(name)}:(.*)$", db, re.M)
            return m.group(1).split() if m else []

        # Transitive, so reintroducing an aggregate (`test-all`) still counts.
        closure, stack = set(), ["gates"]
        while stack:
            for p in prereqs(stack.pop()):
                if p not in closure:
                    closure.add(p)
                    stack.append(p)

        missing = sorted(ci_targets - closure)
        assert not missing, (
            f"`make gates` does not run {missing}, which CI does. Its help "
            "says it runs every gate that guards a merge, so a developer who "
            "trusts it before pushing gets a false green. Add them to "
            "GATES_DEPS in the Makefile."
        )

    def test_lint_job_uses_python_that_can_install_mdformat(self):
        """`make lint-mdformat` self-skips below 3.10 — CI must not skip."""
        ci = _read(CI)
        block = ci[ci.index("\n  lint:") : ci.index("\n  ci-passed:")]
        m = re.search(r'python-version: "(\d+)\.(\d+)"', block)
        assert m, "lint job pins no python-version"
        assert (int(m.group(1)), int(m.group(2))) >= (3, 10), (
            "the lint job needs Python >=3.10 or mdformat self-skips and the "
            "gate silently checks nothing"
        )
