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

and `make gates-declared-check` refuses a branch that drops one, so the list
cannot silently shrink. `make gates-index` prints what is there now;
`~/.claude/hooks/maintainer-role.sh` calls it at session start, which is the
one moment it can still change what someone does.

The floor that check holds the list to is DERIVED too (gh-2038): it is the
list at the branch's merge base, read by this same parse from git's copy of
the base. It used to be a committed file, one sorted line per gate, and
every two PRs adding a gate conflicted there -- four hand rebases in one
batch over a file that held no decision. Now a new gate is protected the
moment it merges, with no second file to touch, and a branch that retires
one on purpose says so where a reviewer reads it: a ``Gate-Retired:``
trailer on one of its commits.

Deliberately ONE line each. This is an index, not documentation -- the
reason lives in the gate's own docstring, where the person who trips it is
already looking.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

#: The declaration. Indented continuations belong to it, so a wrapped
#: obligation stays one entry.
_GATE = re.compile(r"^GATE:\s*(.+)$")

#: What may declare a gate: a test module directly under ``tests/``, and a
#: ``# GATE:`` comment in one of these makefiles. Read from the working tree
#: and, for the floor, from git's copy of the base -- one list for both.
TESTS_GLOB = "test_*.py"
MAKEFILES = ("Makefile", "local.mk")

#: How a branch retires a gate on purpose: a trailer on one of its commits,
#: naming the gate as the refusal prints it.
RETIRED = re.compile(r"^Gate-Retired:[ \t]*(\S.*?)[ \t]*$", re.M)


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
        for path in tests.glob(TESTS_GLOB)
        if obligation(path.read_text(encoding="utf-8"))
    ]
    # Makefile gates ride the same ratchet. Leaving them out would have
    # left `changelog-check` -- the first rule this index was written for --
    # free to lose its declaration silently.
    # The whole text, stripped. A truncated key cut mid-word left a
    # trailing space that the old floor file's parser then stripped, so the
    # set never matched itself across one write/read -- a key has to
    # survive the round trip it is named through, now a `Gate-Retired:`
    # trailer, whose parse strips it too.
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
    for name in MAKEFILES:
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
    for path in sorted(tests.glob(TESTS_GLOB)):
        text = path.read_text(encoding="utf-8")
        got = obligation(text)
        if got:
            out.append((path.stem, got))
    out += makefile_obligations(tests.parent)
    return out


# ── The ratchet ──────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )


def merge_base(root: Path, ref: str) -> str:
    """Where this branch left *ref*: the floor is the gates declared there.

    Fails closed, as `changelog-check` does with the same question: a check
    that cannot reach its reference has not passed. CI's Lint job fetches
    the whole history (``fetch-depth: 0``) and passes the PR's base SHA.
    """
    r = _git(root, "merge-base", "HEAD", ref)
    if r.returncode:
        raise SystemExit(
            f"gates-declared-check: no merge base with {ref} -- fetch it"
            " (CI needs fetch-depth: 0) or set GATES_BASE."
        )
    return r.stdout.decode().strip()


def gates_at(root: Path, rev: str) -> "list[str]":
    """`declared_gates` over the tree at *rev*.

    The same parse, over git's copy of the files it reads, so the floor and
    the tree it is compared with cannot be read two ways -- the old
    committed floor was written by whatever parser last ran the update.
    """
    listed = _git(root, "ls-tree", "-r", "--name-only", rev).stdout.decode()
    paths = [
        p
        for p in listed.splitlines()
        if p in MAKEFILES
        or (
            str(PurePosixPath(p).parent) == "tests"
            and fnmatch(PurePosixPath(p).name, TESTS_GLOB)
        )
    ]
    if not paths:  # `git archive` with no path archives everything
        return []
    tar = _git(root, "archive", "--format=tar", rev, "--", *paths).stdout
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "tests").mkdir()
        with tarfile.open(fileobj=io.BytesIO(tar)) as members:
            for member in members.getmembers():
                data = members.extractfile(member)
                if data is not None:
                    (base / member.name).write_bytes(data.read())
        return declared_gates(base / "tests")


def retired(root: Path, base: str) -> "set[str]":
    """The gates this branch's commits retire, one ``Gate-Retired:``
    trailer each.

    >>> sorted(RETIRED.findall("fix: x\\n\\nGate-Retired: test_a \\n"))
    ['test_a']
    """
    log = _git(root, "log", "--format=%B", f"{base}..HEAD").stdout.decode()
    return set(RETIRED.findall(log))


def _where(name: str) -> str:
    """The file a gate is declared in, from its name."""
    if ":" in name:  # a makefile gate, keyed by its text
        return name.split(":", 1)[0]
    return f"tests/{name}.py"


def check(root: Path, base_ref: str) -> int:
    """Refuse a gate declared at the merge base that the tree no longer
    declares, unless a commit on this branch retires it.

    Both sides are derived, so a new gate needs nothing but its own file:
    it is in the floor the moment it merges. That is what the committed
    floor got wrong twice over -- every two gate PRs conflicted on it, and
    a new gate had to be recorded in it or the ratchet protected nothing
    (37 sat unrecorded for weeks before that was refused).

    The tree, not HEAD: an uncommitted drop is refused before it is
    committed. On a push to main the merge base is HEAD itself, so only
    such a drop can fail there; the PR run, against its base, is the one
    that stops a committed one -- as with `changelog-check`.
    """
    base = merge_base(root, base_ref)
    now = set(declared_gates(root / "tests"))
    floor = set(gates_at(root, base))
    lost = sorted(floor - now - retired(root, base))
    if lost:
        print(
            "error: these gates no longer declare what they enforce, so"
            "\n`make gates-index` has stopped telling anyone about them:\n",
            file=sys.stderr,
        )
        for name in lost:
            print(f"  {name}  (declared in {_where(name)})", file=sys.stderr)
        print(
            "\nEither restore the `GATE:` line where it was declared, or --"
            "\nif the gate is genuinely gone -- say so in a commit on this"
            "\nbranch, one trailer line per gate, so the removal is reviewed"
            "\nrather than silent:\n",
            file=sys.stderr,
        )
        for name in lost:
            print(f"  Gate-Retired: {name}", file=sys.stderr)
        print(file=sys.stderr)
        return 1
    print(
        f"gates-declared-check: {len(now)} declared, none lost since"
        f" {base[:12]}"
    )
    return 0


def main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="refuse a gate the merge base declares and the tree does not",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="what the branch is measured against (default: origin/main)",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if args.check:
        return check(root, args.base)

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
