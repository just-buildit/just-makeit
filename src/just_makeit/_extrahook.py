"""Hand-written ``*_extra.c`` / ``*_prologue.c`` hooks that nothing includes.

A generated ``_ext.c`` pulls in the hand-written hooks sitting beside it, so a
project can add code that survives regeneration. jm never creates or modifies
those files — it only wires them in. The failure this module reports is the one
where it does not: the file exists, compiles into nothing, and no command says
so.

There are two ways to arrive there, and they need different advice:

* **The kind has no hook at all.** ``kind = "handle"`` and ``kind = "capsule"``
  emit an ``_ext.c`` that includes none, so a file placed by analogy with an
  object module is inert (gh-1202). Building the hook for them is a separate
  question with no requester; saying so is not.
* **The generated file is behind.** Every other shape *does* wire one in, but
  the include is decided when the ``_ext.c`` is rendered, and whether a hook
  exists is a fact about the DIRECTORY rather than about the manifest — which
  is the one thing ``apply``'s change detection does not look at. So writing
  the hook after the component exists (the only order available, since the
  component must exist first) leaves it unwired until something else forces a
  re-render.

Both are the same symptom to whoever wrote the file, so both are found the same
way: **ask the artifact.** For each generated ``_ext.c``, look at the hooks
beside it and check whether that file includes each one. Nothing here models
which kinds support a hook or how the include is spelled — a table of kinds
would go stale the moment a kind is added, and a table of spellings is the
renderer's job. A hook a generated file does not name is unreachable whatever
the reason, which is exactly the question worth asking.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Suffixes jm treats as a hand-written hook. Both are documented as "jm never
#: creates or modifies" files, which is what makes an unwired one a silent
#: loss rather than stale output jm would fix on its own.
HOOK_SUFFIXES = ("_extra.c", "_prologue.c")


def _includes(text: str, name: str) -> bool:
    """Does *text* ``#include`` the file *name*?

    Anchored at the start of a line so a mention inside a comment — and the
    generated files are full of prose about these hooks — is not read as a
    wiring. That is the gh-1146 shape: an EXAMPLE of what a detector looks for
    can blind the detector.
    """
    pat = re.compile(
        r'^\s*#\s*include\s*"' + re.escape(name) + r'"', re.MULTILINE
    )
    return bool(pat.search(text))


def unwired_hooks(root: Path) -> list[tuple[Path, Path]]:
    """Every ``(hook, ext_c)`` pair where *ext_c* does not include *hook*.

    Walks ``native/src/*/`` rather than the manifest, because the file being
    looked for is not in the manifest and the shapes that can carry one are
    not worth enumerating. A directory with no generated ``_ext.c`` is skipped:
    there is nothing there that could have included anything, so reporting it
    would be about a project layout jm did not create.

    Returns paths relative to *root*, sorted, so the caller's output is stable.
    """
    src = root / "native" / "src"
    if not src.is_dir():
        return []
    found: list[tuple[Path, Path]] = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        ext_c = d / f"{d.name}_ext.c"
        if not ext_c.is_file():
            continue
        text = ext_c.read_text(encoding="utf-8", errors="replace")
        for hook in sorted(d.iterdir()):
            if not hook.is_file() or not hook.name.endswith(HOOK_SUFFIXES):
                continue
            if not _includes(text, hook.name):
                found.append((hook.relative_to(root), ext_c.relative_to(root)))
    return found


def describe(hook: Path, ext_c: Path) -> str:
    """The one-line report for an unwired hook.

    Deliberately does NOT tell the reader to run ``jm regenerate``. That is the
    command which re-renders the file and would therefore wire the hook in --
    and it deletes the hook while doing so (gh-1216), after which this warning
    disappears because the file is gone. Advice is a claim; this one was
    measured before it was written, and the obvious remedy is the destructive
    one.
    """
    return (
        f"{hook} is not included by {ext_c.name}, so nothing compiles it. "
        f"jm wires a hand-written hook in only when it renders that file, and "
        f"whether one exists is a fact about the directory rather than the "
        f"manifest -- so a hook written after its component is not picked up "
        f"until something re-renders. `jm apply` does that for an object "
        f'module. For `kind = "handle"` / `kind = "capsule"` there is no '
        f"hook at all (gh-1202): put the member on a composer's "
        f"`[[module.X.extra_methods]]`, or use an object module, whose "
        f"per-object fragment is sacred. Do NOT reach for `jm regenerate` -- "
        f"it deletes the hook (gh-1216)."
    )


#: Deduplicated per process, like `_keys._SEEN`: `apply` loads the real tree
#: and its temp scaffold, and a reader should hear each thing once.
_SEEN: set[str] = set()


def warn_unwired_hooks(root: Path, stream=None) -> list[str]:
    """Report every hand-written hook nothing includes. Returns the messages.

    **Advisory, never gating.** For a `kind = "handle"` / `"capsule"` module
    there is no hook to wire, so the finding cannot be cleared by the person
    reading it -- and a gate that refuses suppression is right only while its
    finding CAN be fixed. Marking this as drift would turn every downstream
    with such a file permanently red over a feature jm has not built.
    """
    from . import _report

    out: list[str] = []
    for hook, ext_c in unwired_hooks(root):
        text = describe(hook, ext_c)
        if text in _SEEN:
            continue
        _SEEN.add(text)
        _report.warn(text, gates=False, stream=stream)
        out.append(text)
    return out
