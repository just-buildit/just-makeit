"""The lint toolchain has one source of truth per concern — and stays that way.

Three files each own exactly one thing:

- ``pyproject.toml`` (``[dependency-groups] dev``) — WHICH tools, WHAT versions,
  locked by ``uv.lock``.
- ``Makefile`` — HOW a tool is invoked (binary + flags + paths).
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

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
MAKEFILE = ROOT / "Makefile"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# Tools whose version is pinned in pyproject's dev group; they must be invoked
# through the Makefile so the pinned version is the one that actually runs.
MANAGED_TOOLS = ("ruff", "mdformat")


def _read(p):
    return p.read_text(encoding="utf-8")


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
        mk = _read(MAKEFILE)
        assert re.search(rf"^{re.escape(target)}:", mk, re.M), (
            f"`make {target}` is referenced by CLAUDE.md, the hooks, or CI"
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
