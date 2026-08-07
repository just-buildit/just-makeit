"""One place that decides how heavily a warning reads.

Every warning jm prints during `jm apply` looked identical: same `warning:`
prefix, same weight, in the order the work happened to reach them. Most are
advisory — `apply` is about to fix the thing, or the difference is one only
the author can settle and no command will clear. A couple are not: they are
the same conditions `jm status --check` fails on, so leaving them unactioned
means CI goes red, or worse, that a published artifact is wrong.

That distinction was invisible, and its absence has a measured cost. doppler
shipped a constructor that raised when called as documented for months. The
warning naming that file and that reordering was correct and printed on every
single apply — inside a block of a dozen warnings about fragments that were
fine. Nobody was careless; the signal simply had no more weight than its
neighbours.

`jm status` gained the distinction in gh-823 (`!` gates, `~` does not). This
carries the same idea to `apply`'s stderr, where the finding is actually
*actionable* — `apply` is the command that can fix it — and adds the count as
a trailer, because "3 of the warnings above fail the gate" is what survives a
long scroll when individual lines do not.

Deliberately not a severity *system*. Two weights, one question: will
`jm status --check` fail on this? Anything finer would need a policy nobody
has asked for, and a third level is how the second stops meaning anything.
"""

from __future__ import annotations

import sys

#: Marks a warning whose condition `jm status --check` counts as drift. The
#: same characters `jm status` uses in its own listings, so a reader who has
#: seen one recognises the other.
_GATE_MARK = "!"
_ADVISORY_MARK = "~"

#: Gating warnings emitted since the last :func:`reset`. Module state because
#: the emitters are scattered across `_docsync`, `_apply` and `_object`, and
#: threading a counter through every one of them would be a worse trade than
#: a counter in a single-command process.
_gating = 0


def reset() -> None:
    """Zero the gating count. Called at the start of a command that reports."""
    global _gating
    _gating = 0


def gating_count() -> int:
    """How many gating warnings have been emitted since :func:`reset`."""
    return _gating


def warn(
    text: str, *, gates: bool = False, stream=None, indent: str = ""
) -> None:
    """Print one warning, weighted.

    Parameters
    ----------
    text : str
        The message, without a ``warning:`` prefix — this owns the prefix so
        the two weights cannot drift into being spelled differently.
    gates : bool
        True when `jm status --check` counts this condition as drift. The
        honest test is whether the condition reaches `drift_count`, not
        whether it feels important: a warning marked as gating that does not
        fail the gate teaches the reader to ignore the mark, which is the
        failure this exists to remove.
    stream : file-like, optional
        Defaults to stderr. One caller reports on stdout, inside `apply`'s
        own output block, and stays there — the mark is the point, and
        silently moving a line between streams would break anyone parsing
        either.
    indent : str
        Leading whitespace, for a warning nested in a report block.
    """
    global _gating
    if gates:
        _gating += 1
    mark = _GATE_MARK if gates else _ADVISORY_MARK
    print(f"{indent}warning {mark}: {text}", file=stream or sys.stderr)


def trailer(stream=None) -> None:
    """Summarise the gating warnings, if any. Emits nothing when there are
    none, so a clean run gains no noise."""
    if not _gating:
        return
    n = _gating
    print(
        f"\n{n} of the warning(s) above fail `jm status --check` "
        f"(marked `{_GATE_MARK}`). The rest are advisory.",
        file=stream or sys.stderr,
    )
