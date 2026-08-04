"""_codecheck.py — report authored ``@code`` lines too wide for their stub.

gh-752. An author writes an example inside a C comment and wraps it to the
header's 79 columns, as their C style requires. jm strips the ``` * ```
decoration (3 columns) and re-indents the line to sit inside a docstring (8
columns for a class member, 4 for a module-level function). A line that is 77
columns and correct in the header therefore arrives at 82 in the stub.

Measured on doppler: **160 of its 164** remaining over-79 stub lines are
exactly this, over by the indent or less. The author is not being careless —
they are following the only rule they can see, and it is 8 columns looser than
the one the output has to satisfy.

Why this reports instead of fixing
----------------------------------
The lines are the author's, twice over. They are **doctests**, so re-wrapping
a ``>>>`` changes what runs; and the overflow is overwhelmingly a *trailing
aligned comment* whose column the author chose. ``_pyfmt`` never touches a
multi-line docstring interior for the same reason. So jm names the line, the
column count and the target, and the edit happens in the header where the
text lives.

Why it cannot be a downstream check
-----------------------------------
The budget depends on the destination indent, which is not visible from the
header — and *which* ``@code`` blocks surface, and at what indent, is jm
-internal. doppler tried to reconstruct the list from outside with three
header-side heuristics and got 295 / 267 / 164 depending on scoping. Only jm
knows, so only jm can state the concrete per-site number.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import _config as C
from ._docstring import STUB_TARGET_WIDTH, example_budget

# Where a documented function's examples land in the generated stub. Getting
# this wrong is not a rounding error: at indent 8 the budget is 71 and at
# indent 4 it is 75, so a mis-attributed block reports lines that fit as
# overflowing. Cross-checked against doppler's on-disk stubs -- scanning with
# a flat indent of 8 produced 31 phantom hits, every one of them a `create`
# block whose examples are the *class* docstring's, not a member's.
_MEMBER_INDENT = 8  # a method or property inside a class
_CLASS_INDENT = 4  # the class docstring (a `create` block's @code)
_FUNCTION_INDENT = 4  # a module-level free function


@dataclass(frozen=True)
class Overflow:
    """One ``@code`` line that will not fit its generated docstring."""

    header: Path
    symbol: str  # the C function the block documents
    line: str  # the offending example line, verbatim
    columns: int  # what it will occupy in the stub
    budget: int  # what it must fit in

    def describe(self, root: Path | None = None) -> str:
        """A one-line report naming the site, the overflow and the target."""
        where = self.header
        if root is not None:
            try:
                where = self.header.relative_to(root)
            except ValueError:
                pass
        return (
            f"{where}: {self.symbol}(): @code line will be {self.columns} "
            f"columns in the stub; wrap at <= {self.budget}.\n"
            f"    {self.line}"
        )


def _blocks_for(root: Path, obj: str) -> dict:
    """Doc blocks for one component, via the one loader that already exists."""
    from ._object import _load_doc_blocks

    return _load_doc_blocks(root, obj) or {}


def _module_blocks(root: Path, module: str) -> dict:
    """Doc blocks from a module header, via the loader `_stubs` already uses."""
    from ._object import _load_module_doc_blocks

    try:
        return _load_module_doc_blocks(root, module) or {}
    except (OSError, ValueError):
        return {}


def _example_sources(root: Path, cfg: dict) -> dict[str, tuple[Path, str]]:
    """Map each authored ``@code`` line to the header and symbol it came from.

    Keyed on the stripped line text, which is what survives into the stub
    verbatim. A line that appears in two blocks resolves to the first — the
    attribution is a convenience for the author, and both sites need the same
    edit anyway.
    """
    from ._object import _load_doc_blocks, _load_module_doc_blocks

    src: dict[str, tuple[Path, str]] = {}

    def _add(blocks: dict, header: Path) -> None:
        for symbol, block in blocks.items():
            for ex in getattr(block, "examples", None) or []:
                key = ex.strip()
                if key:
                    src.setdefault(key, (header, symbol))

    for obj in C.components(cfg):
        _add(
            _load_doc_blocks(root, obj) or {},
            root / "native" / "inc" / obj / f"{obj}_core.h",
        )
    for mod in C.modules(cfg):
        try:
            blocks = _load_module_doc_blocks(root, mod) or {}
        except (OSError, ValueError):
            continue
        _add(
            blocks, root / "native" / "inc" / f"{C.module_paths(mod).cname}.h"
        )
    return src


def scan(
    root: Path, cfg: dict, width: int = STUB_TARGET_WIDTH
) -> list[Overflow]:
    """Every authored ``@code`` line that overflows in a generated stub.

    Measured on the emitted ``.pyi`` rather than predicted from the header.
    That ordering is the whole design:

    * **no false positives.** Predicting required guessing which blocks
      surface and at what indent, and the guess was wrong three ways on
      doppler — a `create` block renders into the *class* docstring (indent
      4, not 8), a ``manual_stub`` member renders no docstring at all, and a
      module free function lives in a different header. Each wrong guess is
      an author sent to edit a line that was already fine;
    * **every producer is covered**, including the handle and capsule stubs
      that bypass other passes (gh-747), because the check reads output
      rather than enumerating renderers;
    * **the column count is a measurement.** doppler asked for the concrete
      per-site figure precisely because a global number cannot be acted on,
      and a measured one cannot drift from what the file actually contains.

    The header attribution is then a lookup: the example text reaches the
    stub verbatim, so the emitted line is its own key back to the block that
    wrote it.
    """
    sources = _example_sources(root, cfg)
    out: list[Overflow] = []
    for stub in sorted((root / "src").rglob("*.pyi")):
        for raw in stub.read_text(encoding="utf-8").split("\n"):
            line = raw.rstrip()
            stripped = line.strip()
            if len(line) <= width or not stripped.startswith((">>>", "...")):
                continue
            src = sources.get(stripped)
            if src is None:
                continue  # jm's own synthesised demo, not an authored @code
            header, symbol = src
            indent = len(line) - len(line.lstrip())
            out.append(
                Overflow(
                    header,
                    symbol,
                    stripped,
                    len(line),
                    example_budget(indent, width),
                )
            )
    return out


def report(root: Path, cfg: dict, *, limit: int = 5) -> int:
    """Print a warning per overflowing ``@code`` line; return the count.

    Truncates the listing at *limit* so a project with a large backlog does
    not bury the rest of a command's output — but always prints the **total**,
    because a count that silently shows only its first few reads as a small
    problem. Returns the full count either way, so a caller can report it
    without re-scanning.
    """
    found = scan(root, cfg)
    if not found:
        return 0
    print(
        f"\nWARNING: {len(found)} authored @code line(s) will exceed 79 "
        f"columns in the generated stub."
    )
    for ov in found[:limit]:
        print(f"  {ov.describe(root)}")
    if len(found) > limit:
        print(f"  ... and {len(found) - limit} more.")
    print(
        "  These are yours to edit — jm never rewrites an authored example "
        "(a\n  doctest's `>>>` line and its aligned trailing comment are both "
        "load-bearing).\n  Trim the trailing comment, `...`-continue the "
        "statement, or move the note\n  to a prose line above the `>>>`."
    )
    return len(found)
