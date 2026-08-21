"""_ctorsig.py — the manifest's create() against the one the header declares.

gh-1076. `jm status --check` compares the manifest against the files jm
**owns**, and every one of them can be self-consistent while being wrong: the
stub, the binding and the aggregator all render from the same `init_params`,
so they agree with each other by construction. The only file that can disagree
is the sacred `_core.c` — which jm never reads — and the `_core.h` declaration
it is compiled against.

Measured on 0.63.3, and the measurement is sharper than the report. Reordering
a hand-written `create()`'s parameters:

* on a **standalone** object, `_core.h` is a manifest-owned file, so the
  whole-file diff catches it and `jm status` reports `STALE`;
* on a **module** object, the same file with the same edit reports
  ``OK — up to date``.

One file, one edit, caught in one layout and invisible in the other. doppler
carried exactly that — a manifest declaring one `float[]` param against a C
constructor taking ``(size_t num_taps, const float *h)`` — for long enough
that it was found by reviewing an unrelated jm change rather than by any gate.

Why it matters more than the one object: the manifest is what
`jm regenerate` and every future reconciliation read. While the two disagree,
regenerating writes a `create()` that does not compile. The trap stays armed
for whoever regenerates next.

What this checks, and what it deliberately does not
---------------------------------------------------
jm **injects** the `<comp>_create(...)` declaration into the sacred header, so
it already holds the rendered form. This asks it to verify what it injected
rather than only write it — one targeted comparison that does not depend on
the whole-file diff, and therefore answers the same way in both layouts.

`_core.c` is not read. It does not need to be: a definition that disagrees
with its own header will not compile, so checking the header puts the
definition under the compiler's gate for free. That is the whole reason this
is worth doing at the header rather than by parsing C.

Which side is stale is not decidable here, exactly as with the gh-442
init-param default drift this sits beside — jm cannot know whether the author
meant to change the C or forgot to change the manifest. So the finding names
both and asks for one to move, and is never suppressed: an unsuppressible
finding is right when the alternative is a project that cannot be regenerated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import _config as C


@dataclass(frozen=True)
class CtorDrift:
    """A `create()` the header declares differently from the manifest."""

    component: str
    rel: str  #: POSIX path of the header, relative to the project root
    declared: str  #: the parameter list as the header spells it
    rendered: str  #: the parameter list the manifest renders

    def describe(self) -> str:
        """One warning, naming both sides and what to do about it."""
        return (
            f"{self.rel}\n"
            f"  {self.component}_create() takes different parameters in the"
            " header than the\n"
            "  manifest renders. jm cannot tell which side is stale.\n"
            f"    header:   ({self.declared})\n"
            f"    manifest: ({self.rendered})\n"
            "  Fix one to match. Until they agree, `jm regenerate"
            f" {self.component}` writes a\n"
            "  create() that does not compile against your _core.c."
        )


def _norm(params: str) -> str:
    """One spelling for a C parameter list, so only real differences show.

    Collapses runs of whitespace and pulls each ``*`` onto its pointee, so
    ``const float* h``, ``const float *h`` and ``const  float  *  h`` compare
    equal. **Order is not normalised** — order is the entire finding.

    Examples
    --------
    >>> _norm("const float* h,size_t  h_len")
    'const float *h, size_t h_len'
    >>> _norm("void")
    'void'
    >>> _norm("size_t h_len, const float *h") == _norm("const float *h, size_t h_len")
    False
    """
    parts = []
    for p in params.split(","):
        p = re.sub(r"\s+", " ", p).strip()
        p = re.sub(r"\s*\*\s*", " *", p)
        parts.append(p)
    return ", ".join(x for x in parts if x)


def declared_params(root: Path, component: str, create_fn: str) -> str | None:
    """The parameter list ``<comp>_core.h`` declares for *create_fn*.

    Returns
    -------
    str or None
        ``None`` when there is nothing to compare against — the header is
        absent, unreadable, or carries no declaration of this symbol.

    ``None`` is deliberately distinct from ``""`` (a declaration taking no
    parameters, spelled ``void``). A check that cannot read its input has not
    passed, and collapsing the two is how a gate comes to report clean over a
    tree it never looked at — the shape gh-1033 was filed for one module over.
    """
    path = root / "native" / "inc" / component / f"{component}_core.h"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # The declaration jm injects, as it injects it: the state pointer return,
    # the symbol, and everything up to the closing paren. Anchored to a
    # statement start so a `create()` named inside a Doxygen block or an
    # example line is not mistaken for the declaration.
    m = re.search(
        rf"^\s*{re.escape(component)}_state_t\s*\*\s*"
        rf"{re.escape(create_fn)}\s*\(([^;]*?)\)\s*;",
        text,
        re.M | re.S,
    )
    if m is None:
        return None
    return _norm(m.group(1))


def drift(root: Path, cfg: dict) -> list[CtorDrift]:
    """Every component whose header and manifest disagree about `create()`.

    Silent for a component jm cannot render (a manifest shape the context
    builders reject is a different finding, already reported elsewhere) and
    for one whose header declares no `create()` at all — `no_ctor` and
    `no_state` shapes legitimately have none.
    """
    from . import _glue

    pkg = C.project_name(cfg) or ""
    out: list[CtorDrift] = []
    for comp in C.components(cfg):
        try:
            ctx = _glue.component_ctx(cfg, comp, pkg, root)
        except Exception:
            # A manifest jm cannot render is not this check's finding to
            # report, and raising here would replace a specific diagnostic
            # somewhere else with a traceback out of `jm status`.
            continue
        rendered = ctx.get("create_params")
        if not rendered:
            continue
        create_fn = ctx.get("create_fn") or f"{comp}_create"
        declared = declared_params(root, comp, create_fn)
        if declared is None:
            continue
        if declared == _norm(rendered):
            continue
        out.append(
            CtorDrift(
                component=comp,
                rel=f"native/inc/{comp}/{comp}_core.h",
                declared=declared,
                rendered=_norm(rendered),
            )
        )
    return out
