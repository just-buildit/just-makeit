"""``just-makeit adopt`` — make a module's binding fragment jm's content.

A standalone object's ``<comp>_ext.c`` is glue: rendered whole on every
apply, drift-gated, fixes delivered. A module object's
``<mod>_ext_<obj>.c`` is the *same generated wrapper code* and is treated
as the opposite kind -- created once, thereafter only ever gaining missing
members. So **where a wrapper lives decides whether it receives fixes**
(gh-1448).

What that costs, measured on doppler at 0.82.2: 98 fragments, 89 differing
from a fresh render, **62 of them holding no hand-written code at all**.
What they had missed was not optional -- the ``out=`` contiguity guard
(doppler#1440), the gh-219 use-after-free fix, keyword arguments on module
methods. Per unit across the remaining 52: 1,193 identical, ~60
hand-written. **A 4% hand-written surface freezing the other 96%.**

And it cannot be fixed by re-rendering once: a fragment re-rendered today
matches today, and the next release that changes a wrapper drops it back
into UNRECONCILED with ``--check`` still exiting 0.

This module is the **read-only half**: it answers "what would happen if I
flipped this", and answers it for every fragment at once. Nothing here
writes. The flip itself follows, and lands on these rules rather than
beside them.

Why read-only first
-------------------
doppler's words, and they decide the shape: *"without it the only way to
learn what a flip would do is to do it."* A migration you can inspect only
by performing it is not one anyone should be asked to perform. The listing
is also what a shrink-only ratchet in ``make lint`` reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from . import _config as C
from . import _docsync
from . import _object as O
from . import _render as R


class Verdict(NamedTuple):
    """What `adopt` would do to one fragment, and why.

    ``state`` is one of:

    ``generated``
        already jm's -- the key is set. Nothing to do.
    ``clean``
        every unit matches a fresh render. Flips unattended.
    ``needs_ack``
        units differ in code. jm **cannot tell** a hand-written body from a
        render predating a codegen change -- that is gh-1447's whole
        finding -- so the author acknowledges each, having been shown it.
    ``refused``
        a unit exists only on disk. The render does not produce it, so a
        flip would delete it. Not acknowledgeable: that is not a migration.
    """

    frag: str
    obj: str
    state: str
    differing: tuple
    only_here: tuple


#: The states in which a fragment cannot flip unattended.
_BLOCKING = ("needs_ack", "refused")


def _verdict(frag_rel: str, obj: str, kind: str, ud) -> Verdict:
    if kind == C.FRAGMENT_GENERATED:
        return Verdict(frag_rel, obj, "generated", (), ())
    if ud.only_here:
        return Verdict(frag_rel, obj, "refused", ud.differing, ud.only_here)
    if ud.differing:
        return Verdict(frag_rel, obj, "needs_ack", ud.differing, ())
    return Verdict(frag_rel, obj, "clean", (), ())


def survey(root: Path, cfg: dict, *, only_mod: str | None = None) -> list:
    """A `Verdict` for every module fragment, views included.

    The reference is rendered per fragment through
    `_render.render_module_ext_fragment`, the same call
    `_docsync.refresh_module_fragment_docs` uses -- not a replay tree, and
    not a second renderer. A peer implementation of "what does jm render
    for this object" is the drift this repo keeps paying for.

    A view has no manifest table of its own, so its fragment is governed by
    the key on its PARENT and is reported against that object. That
    grouping is not invented here: `_status` already builds
    ``[obj] + [_view_frag_id(v) for v in C.views(cfg, obj)]``.
    """
    pkg = C.project_name(cfg)
    out: list[Verdict] = []
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        if only_mod is not None and mod != only_mod:
            continue
        ext_dir = root / "native" / "src" / C.module_paths(mod).cname
        for ctx in O.build_component_ctxs(root, cfg, mod, pkg):
            comp = ctx["component"]
            frag_id = ctx.get("frag_id", comp)
            frag = ext_dir / f"{C.module_paths(mod).cname}_ext_{frag_id}.c"
            if not frag.exists():
                continue
            rel = frag.relative_to(root).as_posix()
            ud = _docsync.fragment_unit_diff(
                frag.read_text(encoding="utf-8"),
                R.render_module_ext_fragment(ctx),
            )
            out.append(_verdict(rel, comp, C.fragment_kind(cfg, comp), ud))
    return out


def by_object(verdicts: list) -> dict:
    """Group by the object whose key governs the fragment.

    A parent and its views flip **as a set**: the key lives on the parent,
    so a refusal anywhere in the set refuses the parent.
    """
    out: dict = {}
    for v in verdicts:
        out.setdefault(v.obj, []).append(v)
    return out


def report(verdicts: list) -> int:
    """Print the survey. Returns the exit code.

    Non-zero when anything cannot flip unattended, so it is usable as a
    ratchet. The two blocking states stay **distinct in the output** --
    `refused` is a defect in the flip, `needs_ack` is a decision only the
    author can make.
    """
    groups = by_object(verdicts)
    blocked = 0
    for obj in sorted(groups):
        vs = groups[obj]
        states = {v.state for v in vs}
        worst = next((s for s in _BLOCKING if s in states), None)
        if worst is None:
            label = (
                "already generated"
                if states == {"generated"}
                else "would flip"
            )
            print(f"  {label:20s} {obj}")
            continue
        blocked += 1
        label = "REFUSES" if worst == "refused" else "needs acknowledgement"
        print(f"  {label:20s} {obj}")
        for v in sorted(vs, key=lambda v: v.frag):
            if v.only_here:
                print(f"      {v.frag}")
                for u in v.only_here:
                    print(f"        only here: {u}")
            if v.differing:
                print(f"      {v.frag}")
                for u in v.differing:
                    print(f"        differs:   {u}")
    if not verdicts:
        print("  no module fragments in this project")
    print()
    print(f"  {len(groups) - blocked} of {len(groups)} object(s) could flip")
    if blocked:
        print(
            "  A unit that exists ONLY on disk refuses the flip: the render\n"
            "  does not produce it, so flipping would delete it. Move it to\n"
            "  the `_extra.c` beside the fragment, which is already wired.\n"
            "  A unit that DIFFERS needs your eyes: jm cannot tell a\n"
            "  hand-written body from a render that predates a codegen\n"
            "  change, which is why it asks rather than guesses."
        )
    return 1 if blocked else 0
