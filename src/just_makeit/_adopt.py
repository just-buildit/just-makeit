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
    ahead: tuple


#: The states in which a fragment cannot flip unattended, WORST FIRST.
#: `stale_token` leads: it is not a flip that might lose something, it is a
#: file already saying something false about what apply will do to it.
#:
#: Order is the whole of it: an object is reported by the worst verdict
#: among its fragments, and a refusal must dominate an acknowledgement. It
#: read `("needs_ack", "refused")`, so doppler's `dp_tlm_capture` -- one
#: fragment refusing, a sibling merely differing -- was reported as
#: `needs acknowledgement`, telling a maintainer that looking would be
#: enough when the flip would delete a member (gh-1448 review).
_BLOCKING = ("stale_token", "refused", "needs_ack")


def binding_ahead(existing: str, reference: str) -> tuple:
    """Members whose BINDING accepts more than the manifest declares.

    gh-1448 requirement 2, and it is a REFUSAL rather than a warning: here
    the file is the superset, so re-rendering does not deliver a fix, it
    deletes a feature. doppler's `Resampler.execute_ctrl` takes an `out=`
    (`"OO|O"`) its manifest never declared (`"OO"`); flipping would remove
    it.

    The direction is what the existing report already computes and does not
    use -- `signature_drift_details` prints `binding "OO|O" vs manifest
    "OO"` and then advises deleting the file. Same comparison, read for
    which side is bigger.

    `|` separates required from optional in a PyArg format, so it is
    removed before comparing: what matters is the argument list, not where
    the optional run starts.
    """
    ex = _docsync._method_signatures(existing)
    ref = _docsync._method_signatures(reference)
    out = []
    for name, sig in ref.items():
        if name not in ex:
            continue
        e_fmt = (ex[name][1] or "").replace("|", "")
        r_fmt = (sig[1] or "").replace("|", "")
        if e_fmt != r_fmt and e_fmt.startswith(r_fmt):
            out.append(name)
    return tuple(sorted(out))


def _verdict(
    frag_rel: str, obj: str, kind: str, ud, ahead: tuple = ()
) -> Verdict:
    if kind == C.FRAGMENT_GENERATED:
        return Verdict(frag_rel, obj, "generated", (), (), ())
    if ud.only_here or ahead:
        return Verdict(
            frag_rel, obj, "refused", ud.differing, ud.only_here, ahead
        )
    if ud.differing:
        return Verdict(frag_rel, obj, "needs_ack", ud.differing, (), ())
    return Verdict(frag_rel, obj, "clean", (), (), ())


def flip_verdict(frag_rel: str, obj: str, existing: str, reference: str):
    """Would rendering *reference* over *existing* lose anything?

    THE predicate, shared by `adopt --check` and `apply`'s first-adoption
    guard. They were two, and had already drifted: `--check` knew about
    `differing` units and a binding ahead of its manifest, while `apply`
    refused only a unit that existed on disk alone -- so a hand-keyed
    `wfm_writer` was rewritten with rc 0, losing five accepted
    `sample_type` strings (gh-1448 review). One function cannot disagree
    with itself.
    """
    ud = _docsync.fragment_unit_diff(existing, reference)
    return _verdict(
        frag_rel,
        obj,
        C.FRAGMENT_SACRED,
        ud,
        binding_ahead(existing, reference),
    )


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
            existing = frag.read_text(encoding="utf-8")
            # gh-1448: `generated` is judged by the TOKEN, not the key. A
            # keyed file that has never been rendered whole has not flipped
            # -- it is exactly the first adoption `apply` guards -- so it is
            # evaluated like any other candidate.
            if R.is_owned_render(existing, frag.name):
                # Flipped: exit-neutral, so a ratchet's allow-list is
                # "neither generated nor would flip" and the number it
                # watches is the one that shrinks.
                #
                # Unless the KEY is gone: then the file claims jm
                # regenerates it and jm does not -- the "Hand-patches are
                # preserved" lie, mirrored (gh-1448 review, edge 3).
                state = (
                    "generated"
                    if C.fragment_kind(cfg, comp) == C.FRAGMENT_GENERATED
                    else "stale_token"
                )
                out.append(Verdict(rel, comp, state, (), (), ()))
                continue
            out.append(
                flip_verdict(
                    rel, comp, existing, R.render_module_ext_fragment(ctx)
                )
            )
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
            label = "generated" if states == {"generated"} else "would flip"
            print(f"  {label:20s} {obj}")
            continue
        blocked += 1
        label = {
            "stale_token": "TOKEN WITHOUT KEY",
            "refused": "REFUSES",
        }.get(worst, "needs acknowledgement")
        print(f"  {label:20s} {obj}")
        for v in sorted(vs, key=lambda v: v.frag):
            if v.only_here or v.ahead:
                print(f"      {v.frag}")
                for u in v.only_here:
                    print(f"        only here: {u}")
                for u in v.ahead:
                    print(
                        f"        binding ahead: {u} accepts more than the"
                        " manifest declares"
                    )
            if v.differing:
                print(f"      {v.frag}")
                for u in v.differing:
                    print(f"        differs:   {u}")
    if not verdicts:
        print("  no module fragments in this project")
    print()
    print(f"  {len(groups) - blocked} of {len(groups)} object(s) could flip")
    if any(v.state == "stale_token" for v in verdicts):
        print(
            "  TOKEN WITHOUT KEY: the file says jm regenerates it, but its\n"
            '  object no longer declares `fragment = "generated"`, so\n'
            "  apply leaves it alone. Restore the key, or delete the file\n"
            "  and re-run `jm apply` to get the sacred header back."
        )
    if blocked:
        print(
            "  A unit that exists ONLY on disk refuses the flip: the render\n"
            "  does not produce it, so flipping would delete it. Move it to\n"
            "  the `_extra.c` beside the fragment, which is already wired.\n"
            "  A member whose BINDING accepts more than the manifest\n"
            "  declares refuses too: there the file is ahead, so flipping\n"
            "  removes a feature. Declare it in the manifest first.\n"
            "  A unit that DIFFERS needs your eyes: jm cannot tell a\n"
            "  hand-written body from a render that predates a codegen\n"
            "  change, which is why it asks rather than guesses."
        )
    return 1 if blocked else 0


def stale_tokens(root: Path, cfg: dict) -> list:
    """Fragments carrying an ownership token their object no longer
    declares (gh-1448 review, edge 3).

    The file says jm regenerates it; jm does not, because the key is gone.
    That is the "Hand-patches are preserved" lie mirrored, and it costs the
    same thing: a reader trusting the header about what apply will do.

    Reads files and compares against the manifest -- no render -- so
    `status` can ask it on every run.
    """
    from ._object import _view_frag_id

    out: list = []
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        cname = C.module_paths(mod).cname
        for obj in C.module_objects(cfg, mod):
            if C.fragment_kind(cfg, obj) == C.FRAGMENT_GENERATED:
                continue
            for fid in [obj] + [_view_frag_id(v) for v in C.views(cfg, obj)]:
                name = f"{cname}_ext_{fid}.c"
                f = root / "native" / "src" / cname / name
                if f.is_file() and R.is_owned_render(
                    f.read_text(encoding="utf-8"), name
                ):
                    out.append(f.relative_to(root).as_posix())
    return out
