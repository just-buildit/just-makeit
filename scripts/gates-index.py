#!/usr/bin/env python3
"""The obligations this repo's gates enforce, derived from the gates.

Five times in one session a change was *correct* and its surrounding
contract was not: a `src/` edit with no CHANGELOG entry, a new module with
no row in CLAUDE.md's table, a tagged release with no changelog section, a
test driving the CLI through a child process, and a `_createonly` rule the
fixture could not reach. Every one was caught -- by a gate, after the fact.

None of them would have been caught by a *note*, and this repo has the
receipts for that (`make lint` exists precisely because notes did not
work). But a gate only teaches at the moment it fires, and by then the work
is done. What was missing is the same list, up front.

So this prints it, and **derives it from the gates themselves**. A
hand-written catalogue of gates is a note about notes: it goes stale the
first time a gate is renamed, and it is a second place to forget. Each gate
instead declares its own obligation in its module docstring::

    GATE: every src/ change carries a CHANGELOG entry.

and `make gate-docs-check` fails any repo-wide gate that carries none, so
the list cannot silently shrink. `make gates-index` prints what is there
now; `~/.claude/hooks/maintainer-role.sh` calls it at session start, which
is the one moment it can still change what someone does.

Deliberately ONE line each. This is an index, not documentation -- the
reason lives in the gate's own docstring, where the person who trips it is
already looking.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

#: The declaration. Indented continuations belong to it, so a wrapped
#: obligation stays one entry.
_GATE = re.compile(r"^GATE:\s*(.+)$")


def obligation(source: str) -> str:
    r"""The one-line obligation *source* declares, or ``""``.

    Read from the module docstring rather than a decorator or a registry:
    the docstring is what someone reads when the gate fires, so the two
    cannot drift apart without the drift being visible in one place.

    Examples
    --------
    A module whose whole source is one string literal has that string as
    its docstring, which keeps these examples free of triple quotes.

    >>> obligation("'t.\\n\\nGATE: do the thing.'")
    'do the thing.'
    >>> obligation("'t.\\n\\nGATE: do the thing,\\n      and the other.'")
    'do the thing, and the other.'
    >>> obligation("'nothing declared.'")
    ''
    >>> obligation("not python (")
    ''
    """
    try:
        doc = ast.get_docstring(ast.parse(source)) or ""
    except SyntaxError:
        return ""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        m = _GATE.match(line.strip())
        if not m:
            continue
        parts = [m.group(1).strip()]
        for cont in lines[i + 1 :]:
            if not cont.strip() or not cont.startswith(" "):
                break
            if _GATE.match(cont.strip()):
                break
            parts.append(cont.strip())
        return " ".join(parts)
    return ""


def declared_gates(tests: Path) -> "list[str]":
    """The gate modules carrying an obligation line, sorted.

    The RATCHET's subject. Which files *could* carry one is not derivable,
    and that was measured rather than assumed: selecting "gates that scan
    the repo" gives 109 files when the tell is a bare `.glob(`, and 14 when
    it is a module-level ROOT constant -- and those 14 miss four of the
    five gates that actually caught a real omission in the session this was
    written for. Over-selection makes the index noise; under-selection
    drops a rule silently, which is the one failure a turn-zero index
    cannot have.

    So a human declares, and the ratchet refuses to let the set shrink --
    this repo's own idiom for a judgement no predicate can make.
    """
    names = [
        path.stem
        for path in tests.glob("test_*.py")
        if obligation(path.read_text(encoding="utf-8"))
    ]
    # Makefile gates ride the same ratchet. Leaving them out would have
    # left `changelog-check` -- the first rule this index was written for --
    # free to lose its declaration silently.
    # The whole text, stripped. A truncated key cut mid-word left a
    # trailing space that the floor file's own parser then stripped, so the
    # set never matched itself across one write/read -- a key has to
    # survive the round trip it is stored through.
    names += [
        f"{where}:{what}".strip()
        for where, what in makefile_obligations(tests.parent)
    ]
    return sorted(names)


def makefile_obligations(root: Path) -> "list[tuple[str, str]]":
    """Obligations declared by make-target gates.

    Not every working rule is a pytest. The one that caught the first
    omission in this session's work was `changelog-check`, then a target
    in `local.mk` -- so an index that read only `tests/` would have left out
    the very rule it was written for. A `# GATE:` comment anywhere in a
    makefile declares one, on the same terms.
    """
    out: list[tuple[str, str]] = []
    for name in ("Makefile", "local.mk"):
        path = root / name
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            stripped = line.lstrip("# ").rstrip()
            if not line.lstrip().startswith("#"):
                continue
            m = _GATE.match(stripped)
            if not m:
                continue
            parts = [m.group(1).strip()]
            for cont in lines[i + 1 :]:
                body = cont.lstrip("# ").rstrip()
                if not cont.lstrip().startswith("#") or not body:
                    break
                if _GATE.match(body):
                    break
                parts.append(body)
            out.append((name, " ".join(parts)))
    return out


def collect(tests: Path) -> "list[tuple[str, str]]":
    """Every (gate, obligation) pair, in the order they should be read."""
    out: list[tuple[str, str]] = []
    for path in sorted(tests.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        got = obligation(text)
        if got:
            out.append((path.stem, got))
    out += makefile_obligations(tests.parent)
    return out


def ratchet_path(root: Path) -> Path:
    """Where the floor lives."""
    return root / "tests" / "gates-declared.txt"


def check(root: Path) -> int:
    """Fail when the recorded set and the declared set differ at all.

    A declared gate that lost its obligation, or vanished, is refused: that
    is the ratchet. A NEW gate that is not recorded is refused too. It was
    free once -- "adding one must not need a second commit" -- and 37 gates
    then sat unrecorded for weeks, the whole c_prefix series among them, so
    the ratchet protected none of them. Recording costs no second commit:
    `make gates-index-update` in the same one.
    """
    tests = root / "tests"
    now = set(declared_gates(tests))
    floor_file = ratchet_path(root)
    floor = set()
    if floor_file.exists():
        floor = {
            line.strip()
            for line in floor_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    lost = sorted(floor - now)
    if lost:
        print(
            "error: these gates no longer declare what they enforce, so"
            "\n`make gates-index` has stopped telling anyone about them:\n",
            file=sys.stderr,
        )
        for name in lost:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nEither restore the `GATE:` line in the module docstring, or --"
            "\nif the gate is genuinely gone -- run"
            " `make gates-index-update`\nand commit the result, so the"
            " removal is reviewed rather than silent.\n",
            file=sys.stderr,
        )
        return 1
    gained = sorted(now - floor)
    if gained:
        print(
            "error: these gates declare an obligation but are not recorded,"
            "\nso nothing refuses it if one later drops its `GATE:` line:\n",
            file=sys.stderr,
        )
        for name in gained:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nRun `make gates-index-update` and commit the result with the"
            " gate.\n",
            file=sys.stderr,
        )
        return 1
    print(f"gates-check: {len(now)} declared, all recorded, none lost")
    return 0


def update(root: Path) -> int:
    """Record the current set as the new floor."""
    tests = root / "tests"
    names = declared_gates(tests)
    ratchet_path(root).write_text(
        "# Gates declaring an obligation, via a `GATE:` line in their\n"
        "# module docstring. `make gates-index` prints them; this file is\n"
        "# the ratchet that keeps one from quietly dropping out.\n"
        "# Regenerate with `make gates-index-update`.\n"
        + "\n".join(names)
        + "\n",
        encoding="utf-8",
    )
    print(f"gates-index: recorded {len(names)} declared gate(s)")
    return 0


def main(argv: "list[str]") -> int:
    root = Path(__file__).resolve().parent.parent
    if "--check" in argv:
        return check(root)
    if "--update" in argv:
        return update(root)

    found = collect(root / "tests")
    if not found:
        return 0
    print()
    print("  Before you call a change done:")
    for _, what in found:
        print(f"    - {what}")
    print()
    print("    why each refuses what it refuses: its own docstring")
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
