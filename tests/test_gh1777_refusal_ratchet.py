"""gh-1777: a new deliberate refusal raises ``Refusal``, not ``ValueError``.

The CLI boundary prints a :class:`just_makeit._report.Refusal` as one
``error:`` line and catches nothing else, so a bug still tracebacks. That
only helps the author if jm's refusals ARE ``Refusal``. When gh-1777 landed
jm raised a bare ``ValueError`` from over a hundred sites; the load and
composer owned-pointer ones were converted, and the rest are counted here;
gh-1783 tracks converting them.

A ratchet (CLAUDE.md, habit 6): the count per file may only shrink. A new
``raise ValueError`` -- in a file listed here or one that is not -- fails,
naming the type to use. Converting one fails too, until the count below is
lowered to match, so the ratchet tightens as the debt is paid and never
silently loosens. Registration-free: a new module is scanned the moment it
exists, against an implicit count of zero.

GATE: no file under ``src/just_makeit`` gains a bare ``raise ValueError``.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).parent.parent / "src" / "just_makeit"

#: Rendered or shipped text, not jm's own code: ``templates/`` holds
#: ``<<placeholder>>`` Python that does not parse, ``examples/`` is a user's.
NOT_JM_CODE = ("templates", "examples")

#: Bare ``raise ValueError`` sites per file, as of gh-1777. Lower a number
#: when you convert a site to ``Refusal``; never raise one.
BASELINE = {
    "_apply.py": 6,
    "_bind.py": 8,
    "_capsule.py": 1,
    "_cli_method.py": 2,
    "_cli_object.py": 1,
    "_coerce.py": 1,
    "_composer.py": 7,
    "_config.py": 20,
    "_context/_destroy.py": 11,
    "_context/_diagnostics.py": 1,
    "_context/_methods.py": 7,
    "_context/_parse.py": 4,
    "_context/_sample.py": 6,
    "_context/_state.py": 12,
    "_context/_step.py": 2,
    "_handle.py": 4,
    "_hollow.py": 1,
    "_init.py": 1,
    "_property.py": 1,
    "_record.py": 3,
    "_recorddecl.py": 3,
    "_render.py": 5,
    "_stubs.py": 2,
    "_types.py": 2,
}


def _raises_value_error(node: ast.AST) -> bool:
    """``raise ValueError`` or ``raise ValueError(...)``, any ``from``."""
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exc, ast.Name) and exc.id == "ValueError"


def count_bare_value_errors() -> dict[str, int]:
    """Bare ``raise ValueError`` sites per jm source file, zeros omitted."""
    counts: dict[str, int] = {}
    files = sorted(PKG.rglob("*.py"))
    # A glob that finds nothing would make the gate vacuous.
    assert len(files) > 50, f"only {len(files)} files under {PKG}"
    for path in files:
        rel = path.relative_to(PKG)
        if rel.parts[0] in NOT_JM_CODE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        n = sum(_raises_value_error(node) for node in ast.walk(tree))
        if n:
            counts[rel.as_posix()] = n
    return counts


def test_the_scan_sees_a_bare_raise() -> None:
    """The detector is armed: it counts both spellings, and not Refusal."""
    src = (
        "raise ValueError('a')\n"
        "raise ValueError\n"
        "raise Refusal('b')\n"
        "raise ValueError('c') from None\n"
    )
    tree = ast.parse(src)
    assert sum(_raises_value_error(n) for n in ast.walk(tree)) == 3


def test_no_new_bare_value_error() -> None:
    got = count_bare_value_errors()
    grown = {
        f: (BASELINE.get(f, 0), n)
        for f, n in got.items()
        if n > BASELINE.get(f, 0)
    }
    assert not grown, (
        "a new bare `raise ValueError` (file: (allowed, found)): "
        f"{grown}. A deliberate refusal raises "
        "`just_makeit._report.Refusal`, which the CLI prints as one "
        "`error:` line (gh-1777); a bare ValueError tracebacks."
    )


def test_the_ratchet_is_tight() -> None:
    got = count_bare_value_errors()
    shrunk = {
        f: (n, got.get(f, 0)) for f, n in BASELINE.items() if got.get(f, 0) < n
    }
    assert not shrunk, (
        "fewer bare `raise ValueError` than BASELINE allows "
        f"(file: (allowed, found)): {shrunk}. Lower BASELINE in "
        f"{Path(__file__).name} to match, so the ratchet keeps the gain."
    )
