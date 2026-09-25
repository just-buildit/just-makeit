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

import sys
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
    #: The `differing` units whose render only ADDS code
    #: (`_docsync.render_only_adds`): taking them deletes nothing on disk.
    #: ``--accept-additions`` accepts exactly these; every other differing
    #: unit must be named.
    additions: tuple = ()


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
    frag_rel: str,
    obj: str,
    kind: str,
    ud,
    ahead: tuple = (),
    additions: tuple = (),
) -> Verdict:
    if kind == C.FRAGMENT_GENERATED:
        return Verdict(frag_rel, obj, "generated", (), (), ())
    if ud.only_here or ahead:
        return Verdict(
            frag_rel,
            obj,
            "refused",
            ud.differing,
            ud.only_here,
            ahead,
            additions,
        )
    if ud.differing:
        return Verdict(
            frag_rel, obj, "needs_ack", ud.differing, (), (), additions
        )
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
    ex_units = _docsync.fragment_units(existing)
    ref_units = _docsync.fragment_units(reference)
    additions = tuple(
        u
        for u in ud.differing
        if _docsync.render_only_adds(ex_units[u], ref_units[u])
    )
    return _verdict(
        frag_rel,
        obj,
        C.FRAGMENT_SACRED,
        ud,
        binding_ahead(existing, reference),
        additions,
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
                    if u in v.additions:
                        print(f"        adds only: {u}")
                    else:
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
            "  change, which is why it asks rather than guesses.\n"
            "  `adds only`: the render keeps every token of the unit and\n"
            "  adds code (a guard, keywords) -- nothing on disk is lost.\n"
            "  `jm adopt <obj> --accept-additions` takes those; a `differs`\n"
            "  unit removes code, and is taken only by name:\n"
            "  `--accept <unit>`."
        )
    return 1 if blocked else 0


def adopt(
    root: Path,
    objs: "list[str]",
    *,
    only_mod: "str | None" = None,
    accept: "frozenset[str]" = frozenset(),
    accept_additions: bool = False,
) -> int:
    """Flip each target object whose fragments may flip; refuse the rest.

    The write half of gh-1448. Targets are *objs*, or every object in
    *only_mod*, or -- both empty -- every module object. Each is judged by
    the same `flip_verdict` `--check` prints and `apply`'s first-adoption
    guard applies, so the three cannot disagree about what is safe.

    An object flips, as a SET with its views, when nothing refuses it and
    every differing unit is consented to: named with *accept*, or -- for a
    unit whose render only adds code -- covered by *accept_additions*.
    Consent is this act, on this command line, never a second manifest key.

    **All or nothing per object.** Nothing is written for one that does not
    flip. For one that does: the key is written, its fragments are deleted
    -- every unit in them was either identical to the render or accepted --
    and `apply` renders them whole, ownership token and all. That is the
    walkthrough `stale_project` teaches by hand, in one guarded step.

    Returns the exit code: 1 when any target did not flip.
    """
    from . import _apply

    cfg = C.load(root)
    groups = by_object(survey(root, cfg, only_mod=only_mod))
    for obj in objs:
        if not C.component_module(cfg, obj):
            print(
                f"error: '{obj}' is not a module object -- only a module's"
                " per-object fragment can be adopted; a standalone object's"
                " binding is already jm's.",
                file=sys.stderr,
            )
            return 2
        if obj not in groups:
            print(
                f"error: '{obj}' has no binding fragment on disk; `jm apply`"
                " creates it.",
                file=sys.stderr,
            )
            return 2
    targets = list(objs) or sorted(groups)

    # A consent that matches nothing is a typo, and a typo here is a unit
    # the author believes they accepted. Refused before anything is judged.
    offered = {u for o in targets for v in groups[o] for u in v.differing}
    unmatched = sorted(accept - offered)
    if unmatched:
        print(
            "error: --accept names no differing unit of "
            f"{', '.join(targets)}: {', '.join(unmatched)}.\n"
            "  `just-makeit adopt --check` lists the units.",
            file=sys.stderr,
        )
        return 2

    flips: list = []
    blocked = 0
    for obj in targets:
        vs = groups[obj]
        states = {v.state for v in vs}
        if states == {"generated"}:
            print(f"  already jm's         {obj}")
            continue
        if "stale_token" in states or "refused" in states:
            blocked += 1
            print(f"  REFUSES              {obj}")
            for v in vs:
                for u in v.only_here:
                    print(f"      {v.frag}: only here: {u}")
                for u in v.ahead:
                    print(f"      {v.frag}: binding ahead: {u}")
                if v.state == "stale_token":
                    print(f"      {v.frag}: token without key")
            continue
        pending = [
            (v.frag, u)
            for v in vs
            for u in v.differing
            if u not in accept and not (accept_additions and u in v.additions)
        ]
        if pending:
            blocked += 1
            print(f"  needs acknowledgement {obj}")
            for frag, u in pending:
                print(f"      {frag}: {u}")
            continue
        flips.append(obj)
        print(f"  flips                {obj}")

    if flips:
        for obj in flips:
            C.set_fragment_kind(cfg, obj, C.FRAGMENT_GENERATED)
        C.save(root, cfg)
        for obj in flips:
            for v in groups[obj]:
                if v.state != "generated":
                    (root / v.frag).unlink()
        print()
        _apply.run(root)
    if blocked:
        print(
            f"\n{blocked} object(s) not flipped; nothing was written for"
            " them. `just-makeit adopt --check` shows each unit."
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


# ── packaging templates (gh-1589) ─────────────────────────────────────────────
#
# `cmake/<pkg>.pc.in` and `cmake/<pkg>-config.cmake.in` hold no authored
# content: every value in them arrives through a CMake variable the root
# CMakeLists sets. A project scaffolded since gh-1589 gets them born owned,
# and `apply` renders them whole while the token names the file. A project
# scaffolded before has them token-less, so no fix to either reaches it --
# that is what `status`'s PACKAGING section reports and what
# `adopt --packaging` hands to jm, refusing a file whose adoption would drop
# a line the render does not have.


class PackagingVerdict(NamedTuple):
    """What `adopt --packaging` would do to one packaging template.

    ``state`` is ``owned`` (the token names the file: jm's already),
    ``current`` (token-less, but otherwise today's render: adopting adds
    only the token) or ``behind`` (token-less and different: adopting
    takes today's render). ``lost`` is every line on disk -- blank and
    comment lines aside -- that the render does not have: what adopting
    would drop.
    """

    path: str
    state: str
    lost: "tuple[str, ...]"
    disk: str
    render: str


def packaging_patterns() -> "tuple[str, ...]":
    """The owned-scaffold patterns that are packaging templates."""
    from ._apply import OWNED_SCAFFOLDS

    return tuple(p for p in OWNED_SCAFFOLDS if p.startswith("cmake/"))


def _meaningful(text: str) -> "list[str]":
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


_PKG_TOKEN = __import__("re").compile(r"@\w+@|\$\{\w+\}|\w+|[^\s\w]")


def _kept(line: str, render_lines: "list[str]") -> bool:
    """True when some render line holds every token of *line*, in order.

    gh-1448's structural test (`_docsync.render_only_adds`), per line and
    over a tokenisation that splits a configure slot (``@JM_PC_CFLAGS@``) or
    a ``${var}`` from what surrounds it: an older jm's
    ``Cflags: -I${includedir}`` is kept by today's
    ``Cflags: -I${includedir}@JM_PC_CFLAGS@``, which only adds to it.

    >>> _kept("Cflags: -I${includedir}", ["Cflags: -I${includedir}@X@"])
    True
    >>> _kept("set(MY_HAND 1)", ["check_required_components(p)"])
    False
    """
    toks = " ".join(_PKG_TOKEN.findall(line))
    return any(
        _docsync.render_only_adds(toks, " ".join(_PKG_TOKEN.findall(r)))
        for r in render_lines
    )


def packaging_survey(
    root: Path, replay_root: Path, project: str = ""
) -> "list[PackagingVerdict]":
    """Judge each packaging template in *root* against *replay_root*.

    *replay_root* is jm's current render of the whole project (the tree
    `apply` replays the manifest into); its templates are born owned. A
    template the replay renders and the project lacks is not listed: that
    is MISSING, which `apply` creates.
    """
    out: "list[PackagingVerdict]" = []
    # gh-1589: a line some released jm rendered is an older jm's, not the
    # author's, however today's render has respelled it.
    released = _history_lines(project) if project else set()
    for pattern in packaging_patterns():
        for src in sorted(replay_root.glob(pattern)):
            rel = src.relative_to(replay_root).as_posix()
            dst = root / rel
            if not dst.is_file():
                continue
            disk = dst.read_text(encoding="utf-8")
            render = src.read_text(encoding="utf-8")
            if R.is_owned_render(disk, dst.name):
                out.append(PackagingVerdict(rel, "owned", (), disk, render))
                continue
            head = R.owned_token(dst.name) + "\n" + R.OWNED_PACKAGING_NOTE
            bare = render[len(head) :] if render.startswith(head) else render
            have = _meaningful(render)
            lost = tuple(
                ln
                for ln in _meaningful(disk)
                if ln not in released and not _kept(ln, have)
            )
            state = "current" if disk == bare else "behind"
            out.append(PackagingVerdict(rel, state, lost, disk, render))
    return out


def _fill(project: str):
    names = {
        "<<project_underscore>>": project,
        "<<project>>": project.replace("_", "-"),
    }

    def fill(text: str) -> str:
        for k, v in names.items():
            text = text.replace(k, v)
        return text

    return fill


def _history_lines(project: str) -> "set[str]":
    """Every content line a released jm rendered in a packaging template,
    with *project*'s names in place of the placeholders (gh-1589)."""
    from . import _installhistory

    fill = _fill(project)
    return {fill(ln) for ln in _installhistory.LINES}


def _history_calls(project: str) -> "set":
    """Every install-section command a released jm rendered, with *project*'s
    names in place of the template's placeholders (gh-1589)."""
    from . import _installhistory

    fill = _fill(project)
    return {
        (name, tuple(fill(a) for a in args))
        for name, args in _installhistory.CALLS
    }


def root_install_verdict(
    root: Path, replay_root: Path, cfg: dict
) -> "PackagingVerdict | None":
    """What `adopt --packaging` would do to the root install section.

    ``owned`` when the file already carries the managed block (`apply` keeps
    it). Otherwise the section runs from the ``# ── Install`` header to the
    end of the file, as every older scaffold wrote it, and adopting replaces
    it with the managed block. ``lost`` is each COMMAND in it that neither
    today's block nor any released jm rendered (:mod:`_installhistory`):
    those are the author's, and adopting would drop them. A command an older
    jm rendered is not the author's, however it has since changed.

    ``None`` when there is no root file or no install section to adopt.
    """
    from . import _rootcmake

    real, temp = root / "CMakeLists.txt", replay_root / "CMakeLists.txt"
    if not (real.is_file() and temp.is_file()):
        return None
    disk = real.read_text(encoding="utf-8")
    if _rootcmake.install_block(disk) is not None:
        return PackagingVerdict("CMakeLists.txt", "owned", (), disk, disk)
    begin = _rootcmake.INSTALL_BEGIN.search(disk)
    rendered = temp.read_text(encoding="utf-8")
    rb = _rootcmake.INSTALL_BEGIN.search(rendered)
    re_ = _rootcmake.INSTALL_END.search(rendered)
    if begin is None or rb is None or re_ is None:
        return None
    block = rendered[rb.start() : re_.end()] + "\n"
    render = disk[: begin.start()] + block
    old = _rootcmake.calls(disk[begin.end() :])
    known = _history_calls(C.project_name(cfg)) | {
        (c.name, c.args) for c in _rootcmake.calls(block)
    }
    lost = tuple(
        f"{c.name}({' '.join(c.args)})"
        for c in old
        if (c.name, c.args) not in known
    )
    new = _rootcmake.calls(rendered[rb.end() : re_.start()])
    state = "current" if old == new else "behind"
    return PackagingVerdict("CMakeLists.txt", state, lost, disk, render)


def _packaging_diff(v: PackagingVerdict) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            v.disk.splitlines(keepends=True),
            v.render.splitlines(keepends=True),
            f"a/{v.path}",
            f"b/{v.path} (jm's render)",
        )
    )


def adopt_packaging(
    root: Path,
    cfg: dict,
    *,
    check: bool,
    accept: "frozenset[str]" = frozenset(),
) -> int:
    """`jm adopt --packaging [--check] [--accept <path>]` (gh-1589).

    ``--check`` prints each template's verdict, its diff against today's
    render and the lines adopting would drop, and writes nothing. Without
    it, every template that loses nothing is written as jm's owned render;
    one that would lose a line is refused -- nothing written for it --
    unless its path (or file name) is named by ``--accept``. Returns 1 when
    anything was refused, else 0.
    """
    import contextlib
    import io
    import tempfile

    from . import _apply, _textio

    with tempfile.TemporaryDirectory(prefix="jm-adopt-") as tmp:
        # The temp tree `apply` builds, the way `apply` builds it: a bare
        # `_replay` could not replay a project whose modules reference a
        # capsule declared later (doppler).
        replay_root = Path(tmp) / C.project_name(cfg)
        with contextlib.redirect_stderr(io.StringIO()):
            _apply.replay_project(cfg, replay_root, root)
        verdicts = packaging_survey(root, replay_root, C.project_name(cfg))
        # gh-1589 part 2: the root install section, by the same rules.
        section = root_install_verdict(root, replay_root, cfg)
        if section is not None:
            verdicts.append(section)

    refused = 0
    root_refused = False
    for v in verdicts:
        taken = v.path in accept or Path(v.path).name in accept
        if v.state == "owned":
            print(f"  jm's already        {v.path}")
            continue
        if v.path == "CMakeLists.txt":
            label = "its install section becomes jm's managed block"
        else:
            label = (
                "adds the token"
                if v.state == "current"
                else "takes the render"
            )
        if v.lost and not taken:
            refused += 1
            root_refused |= v.path == "CMakeLists.txt"
            print(f"  REFUSES             {v.path}")
            for ln in v.lost:
                print(f"      would drop: {ln}")
        elif check:
            print(f"  would adopt         {v.path} ({label})")
        else:
            _textio.write_text(root / v.path, v.render)
            print(f"  adopted             {v.path} ({label})")
        if check and v.state == "behind":
            print(_packaging_diff(v), end="")
    if not verdicts:
        print("  no packaging templates in this project")
    if refused:
        print(
            "\n  A line on disk that jm's render does not keep is refused:"
            " adopting would\n"
            "  drop it. jm cannot tell a line you wrote from one an older jm"
            " rendered\n"
            "  and has since respelled, so it asks rather than guesses: read"
            " the diff\n"
            "  (`--check`), move anything of yours into the root"
            " CMakeLists.txt as a\n"
            "  CMake variable the template reads, then take the render with\n"
            "  `jm adopt --packaging --accept <path>`."
            + ("" if check else " Nothing was written for it.")
        )
    if root_refused:
        print(
            "\n  In the root CMakeLists.txt, a refused line is a command no"
            " released jm\n"
            "  rendered in its install section. Move it above the"
            " `# ── Install` line (or\n"
            "  into a file you include() from there), then adopt; after"
            " adopting, your own\n"
            "  install rules go below `# ── End install`, which jm never"
            " writes."
        )
    return 1 if refused else 0
