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
    return subprocess.run(
        ["make", "-rpn", "--no-print-directory"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout


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
        offenders = [
            f"Makefile:{i}: {line}"
            for i, line in enumerate(_read(MAKEFILE).splitlines(), 1)
            if re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*:", line)
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
