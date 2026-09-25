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
from . import _csym
from . import _incpath as INC


@dataclass(frozen=True)
class CtorDrift:
    """A `create()` the header declares differently from the manifest."""

    component: str
    rel: str  #: POSIX path of the header, relative to the project root
    declared: str  #: the parameter list as the header spells it
    rendered: str  #: the parameter list the manifest renders
    csym: str  #: the component's C symbol stem (gh-1591)

    def describe(self) -> str:
        """One warning, naming both sides and what to do about it."""
        return (
            f"{self.rel}\n"
            f"  {self.csym}_create() takes different parameters in the"
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


def split_params(params: str) -> list[str]:
    """The top-level items of a C parameter or argument list.

    One splitter for both sides gh-1502 compares -- the prototype's
    parameters and the ``@code`` example's arguments -- so a count of one
    cannot disagree with a count of the other by construction. Commas inside
    parentheses (a cast, a nested call) do not split, and a list that is
    ``void`` or empty has no items.

    Examples
    --------
    >>> split_params("int level, const float *h, size_t h_len")
    ['int level', 'const float *h', 'size_t h_len']
    >>> split_params("f(a, b), 0")
    ['f(a, b)', '0']
    >>> split_params("void"), split_params("  ")
    ([], [])
    """
    items: list[str] = []
    depth = 0
    cur = ""
    for ch in params:
        if ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        cur += ch
    items.append(cur.strip())
    items = [x for x in items if x]
    return [] if items == ["void"] else items


def _header_text(root: Path, component: str) -> str | None:
    """``<comp>_core.h`` as text, or ``None`` when it cannot be read."""
    path = INC.core_h(root, component)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


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
    text = _header_text(root, component)
    if text is None:
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
        # `create_name`, not `create_fn`: the context publishes an object's
        # override under that key and never sets `create_fn` (gh-1494).
        create_fn = _csym.ctx_create_name(ctx)
        declared = declared_params(root, comp, create_fn)
        if declared is None:
            continue
        if declared == _norm(rendered):
            continue
        out.append(
            CtorDrift(
                component=comp,
                rel=INC.core_rel(comp, root),
                declared=declared,
                rendered=_norm(rendered),
                csym=_csym.stem(cfg, comp),
            )
        )
    return out


@dataclass(frozen=True)
class ExampleDrift:
    """A file-comment example calling `create()` with the wrong arg count.

    gh-1502. The ``@code`` example at the top of ``<comp>_core.h`` is the
    author's text: jm writes it once, at scaffold time, and never again. The
    prototype below it is jm's, and `apply` rewrites it whenever the
    manifest's init params change. So the example is the one line in the
    file guaranteed to fall behind, and nothing compiles it to notice.

    **Advisory, never counted.** The header is sacred: the only way to clear
    a gating finding here would be for the author to retype what jm
    rendered, which is not an action jm may require of author text. The
    count is all this compares -- values and names are the author's to
    choose, and jm cannot tell a stale argument from a deliberate one.
    """

    component: str
    rel: str  #: POSIX path of the header, relative to the project root
    line: int  #: 1-based line of the example's create() call
    call: str  #: the called symbol, as the example spells it
    passed: int  #: arguments the example passes
    declared: int  #: parameters the prototype declares

    def describe(self) -> str:
        """One advisory line pair, naming the line and what to change."""
        return (
            f"{self.rel}:{self.line}\n"
            f"  the @code example calls {self.call}() with {self.passed}"
            f" argument(s); the prototype\n"
            f"  declares {self.declared}. Advisory: the example is your"
            " text, and jm does not\n"
            "  rewrite it. Update the call to match the declaration."
        )


def example_drift(root: Path, cfg: dict) -> list[ExampleDrift]:
    """Every header whose ``@code`` example mis-counts `create()`'s args.

    The example's call is found by what it assigns to -- the object's
    ``<comp>_state_t *`` -- inside the file comment's ``@code`` block, so it
    needs neither the manifest nor the context builders: the comparison is
    the example against the prototype in the SAME file, read by
    `declared_params`. Silent when either side is absent (no example, no
    create call, no prototype), because there is nothing to compare.
    """
    out: list[ExampleDrift] = []
    for comp in C.components(cfg):
        text = _header_text(root, comp)
        if text is None:
            continue
        head = re.match(r"\s*/\*\*.*?\*/", text, re.S)
        if head is None:
            continue
        block = re.search(r"@code\b(.*?)@endcode", head.group(0), re.S)
        if block is None:
            continue
        call = re.search(
            rf"\b{re.escape(comp)}_state_t\s*\*\s*\w+\s*=\s*(\w+)\s*\(",
            block.group(1),
        )
        if call is None:
            continue
        declared = declared_params(root, comp, call.group(1))
        if declared is None:
            continue
        # The arguments run to the paren that closes the call.
        rest = block.group(1)[call.end() :]
        depth, end = 1, None
        for i, ch in enumerate(rest):
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth == 0:
                end = i
                break
        if end is None:
            continue
        passed = len(split_params(rest[:end]))
        want = len(split_params(declared))
        if passed == want:
            continue
        offset = head.start() + block.start(1) + call.start(1)
        out.append(
            ExampleDrift(
                component=comp,
                rel=INC.core_rel(comp, root),
                line=text.count("\n", 0, offset) + 1,
                call=call.group(1),
                passed=passed,
                declared=want,
            )
        )
    return out
