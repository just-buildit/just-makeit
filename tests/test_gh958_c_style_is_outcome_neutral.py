"""gh-958: opting into `c_format_command` must not change what `status` says.

`_cfmt` reformats the regenerated `*_ext.c` glue to the project's own
`.clang-format`, and `_apply` puts the tree it compares against through the
same pass, so both sides converge (gh-493, gh-746). The failure mode when that
symmetry breaks is a project whose `jm status --check` reports drift it did not
cause and cannot clear, which is gh-635's shape and has been re-found several
times (gh-758, gh-917).

Every existing test of this asserts convergence **across `jm apply`** — they
scaffold, apply, then check. That leaves the emission side untested: whether a
command leaves the tree formatted in the first place. gh-958 was filed claiming
it does not, from a scaffold built by calling `_new.run` and `_object.run`
directly. That was wrong, and wrong in a way worth encoding rather than just
correcting:

**Formatting is a post-command hook on the CLI dispatcher**
(`_cli._C_EMITTING_COMMANDS`), not part of emission. `_new.run` formats its own
tree because the hook cannot reach it — its root is a subdirectory, not cwd —
and every other emitter relies on the hook. So driving the private API directly
skips it, while the real CLI is correct. The issue measured the API and
described the CLI.

The gate below therefore drives the **CLI**, and asserts the property that
actually matters and that no list can go stale against:

    declaring `c_format_command` does not change `status --check`'s verdict.

Outcome-neutrality rather than "the files are formatted" on purpose. It needs
no definition of formatted, no list of which commands emit C, and no knowledge
of which files are glue — a command added to the dispatcher and forgotten in
`_C_EMITTING_COMMANDS` shows up as the two legs disagreeing.

It compares the **set of drifting paths**, not the exit code. A one-bit signal
is one any other failure can absorb: `jm method` on a module object already
leaves the tree stale for an unrelated reason (gh-963, the binding fragment it
does not refresh), so an exit code cannot tell a style regression from the
drift that was there anyway. `status --json`'s entries can.

**Measured sensitivity, because guessing at it was wrong twice.** Skipping the
format pass for one command at a time (`if cmd in _C_EMITTING_COMMANDS and
cmd != "<x>"`) and re-running the gate:

    detected      function, warning, error, view
    not detected  method, property, perf, regenerate, split-objects,
                  upgrade, apply

That is not four holes — it is where the hook is load-bearing. `apply` formats
its own replay and syncs the formatted result, so the hook is redundant after
it; `method` and `property` rewrite no glue in the real tree at all (gh-963);
the rest likewise leave nothing unformatted behind. The gate fires exactly
where skipping the pass changes the tree, which is the only place it can.

The coverage grows on its own, too: if gh-963 is fixed so `method` refreshes
the fragment it emits, the hook becomes load-bearing for it and this gate
starts covering it with no edit here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = Path(__file__).parent.parent / "src"
_HAS_CF = shutil.which("clang-format") is not None
_cf_only = pytest.mark.skipif(not _HAS_CF, reason="clang-format not installed")


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=600,
    )


# Every C-emitting command this can drive end to end, with a working argument
# form. Order matters — `method` needs the object, `view` needs the module.
_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "method",
        (
            "method",
            "gain",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
        ),
    ),
    ("property", ("property", "gain", "level", "--type", "double")),
    (
        "function",
        (
            "function",
            "helper",
            "--module",
            "filt",
            "--param",
            "x:float",
            "--return-type",
            "float",
        ),
    ),
    (
        "warning",
        (
            "warning",
            "gain",
            "--condition",
            "level",
            "--message",
            "hi",
            "--module",
            "filt",
        ),
    ),
    (
        "error",
        (
            "error",
            "gain",
            "--category",
            "ValueError",
            "--message",
            "boom",
            "--module",
            "filt",
        ),
    ),
    (
        "view",
        (
            "view",
            "gain",
            "GainView",
            "--module",
            "filt",
            "--create-fn",
            "gain_create_alt",
        ),
    ),
    ("perf", ("perf",)),
    ("regenerate", ("regenerate", "gain")),
    ("split-objects", ("split-objects",)),
    ("upgrade", ("upgrade",)),
    ("apply", ("apply",)),
)

# `_C_EMITTING_COMMANDS` members the matrix above does not drive, each with the
# reason. A ratchet: it may shrink, never grow. `object` and `module` are here
# because the fixture *is* them — every leg scaffolds with both, so they are
# exercised on every single run rather than as a step.
_NOT_STEPPED = {
    "object": "the fixture scaffolds with it",
    "module": "the fixture scaffolds with it",
    "add": "structural — discards the sacred _core.c, so it cannot run "
    "mid-sequence without invalidating the steps after it",
    "remove": "would delete the component the later steps operate on",
    "bind": "reads a foreign header; needs a fixture of its own",
}


def _scaffold(root: Path, *, styled: bool) -> None:
    """A module project, with or without the c_style opt-in.

    Both legs are otherwise identical, which is the whole point: any
    difference in the verdicts below is attributable to the opt-in alone.
    """
    root.mkdir(parents=True)
    args = ["new", "p"]
    if styled:
        args += ["--c-style", "clang-format"]
    assert _cli(*args, cwd=root).returncode == 0
    proj = root / "p"
    assert _cli("module", "filt", cwd=proj).returncode == 0
    assert _cli("object", "gain", "--module", "filt", cwd=proj).returncode == 0


def _drift(proj: Path) -> set[tuple[str, str]]:
    """The (path, state) pairs `status` reports as anything but OK.

    Read from `--json` rather than the exit code — see the module docstring for
    why one bit was not enough.
    """
    r = _cli("status", "--json", cwd=proj)
    assert r.returncode in (0, 1), f"status --json failed:\n{r.stderr}"
    payload = json.loads(r.stdout)
    return {
        (e["path"], e["state"])
        for e in payload["entries"]
        if e["state"] != "ok"
    }


def _verdicts(tmp_path: Path, *, styled: bool) -> dict[str, set]:
    """What `status` reports after each step, keyed by step name."""
    base = tmp_path / ("styled" if styled else "plain")
    _scaffold(base, styled=styled)
    proj = base / "p"
    out: dict[str, set] = {"«scaffold»": _drift(proj)}
    for name, argv in _STEPS:
        assert _cli(*argv, cwd=proj).returncode == 0, f"{name} itself failed"
        out[name] = _drift(proj)
    return out


@_cf_only
def test_declaring_c_format_command_changes_no_verdict(tmp_path):
    """The gate.

    Sabotage, verified: make the CLI hook skip `function`, `warning`, `error`
    or `view` (`if cmd in _C_EMITTING_COMMANDS and cmd != "<x>"`) and that
    step's legs diverge — the styled tree keeps jm's own 4-space emission
    while the replay it is compared against is house-styled, so a path appears
    in the styled leg alone. Leaving the frozenset itself intact is deliberate:
    the bookkeeping test below stays green, so only this one can fire. See the
    module docstring for which commands this cannot fire on, and why that is
    the absence of a bug rather than a hole.
    """
    plain = _verdicts(tmp_path, styled=False)
    styled = _verdicts(tmp_path, styled=True)
    differing = {
        step: {
            "styled only": sorted(styled[step] - plain[step]),
            "plain only": sorted(plain[step] - styled[step]),
        }
        for step in plain
        if plain[step] != styled[step]
    }
    assert not differing, (
        "declaring c_format_command changed what `status` reports: "
        f"{differing}. Formatting must be outcome-neutral — the emitted tree "
        "and the tree `apply` compares it against go through the same pass."
    )


@_cf_only
def test_the_scaffold_itself_is_clean_on_both_legs(tmp_path):
    """gh-958's filed claim, stated directly.

    The issue said a `--c-style` project reports its own untouched scaffold as
    STALE. Through the CLI it does not, and this is the assertion that says so
    — kept separate from the matrix above so a regression here names the
    original claim rather than a step.
    """
    for styled in (False, True):
        base = tmp_path / ("s" if styled else "p")
        _scaffold(base, styled=styled)
        r = _cli("status", "--check", cwd=base / "p")
        assert r.returncode == 0, (
            f"a freshly scaffolded {'c_style ' if styled else ''}project is "
            f"not clean:\n{r.stdout}\n{r.stderr}"
        )


def test_the_unstepped_commands_ratchet_only_shrinks():
    """`_NOT_STEPPED` may lose entries, never gain them.

    Without this, adding a C-emitting command and quietly excusing it from the
    matrix reads exactly like coverage. The matrix plus this set must together
    account for every member of `_C_EMITTING_COMMANDS`, so a new one forces a
    choice: drive it, or write down why not.
    """
    from just_makeit._cli import _C_EMITTING_COMMANDS

    stepped = {name for name, _ in _STEPS}
    accounted = stepped | set(_NOT_STEPPED)
    missing = set(_C_EMITTING_COMMANDS) - accounted
    assert not missing, (
        f"C-emitting command(s) neither driven by _STEPS nor excused in "
        f"_NOT_STEPPED: {sorted(missing)}"
    )
    stale = accounted - set(_C_EMITTING_COMMANDS)
    assert not stale, (
        f"_STEPS/_NOT_STEPPED name command(s) that no longer emit C: "
        f"{sorted(stale)}"
    )
