"""_docgaps.py -- the members whose documentation is only their own name.

gh-1394. doppler's 0.77.2 bump turned 39 property docstrings into name stubs
(``nfft`` -> ``"Nfft."``), and the only way to find them was to diff generated
output. Every one had no documentation of its own: gh-1300 stopped a field
inheriting a same-named field's comment from another struct, so what fell away
was a sentence that belonged to `psd_state_t` or `tone_meas_t`, and what was
left was the gap that had always been there.

A gap is cheap to close -- one ``/**<`` on the field, or ``doc =`` in the
manifest -- once you know it exists. This reports them, and says for each one
where the text would be read from, so the answer is a line to write rather
than a search.

Deliberately NOT a gate. Whether an undocumented property is worth a sentence
is the author's call, and a ratchet here would fail a project the day it
declares a property it has not documented yet -- which is the normal order of
work. `jm status --docs` asks; nothing refuses.

Scope: properties, the fields of a `single` / `record_dtype` record,
methods, and a module's free functions -- each through the one chain its
renderers use, never a second copy of the precedence. Class and module docs
(`[<comp>] doc`, `[module.X] doc`) are not walked yet; gh-1396 tracks that.
"""

from __future__ import annotations

from pathlib import Path

from . import _config as C
from . import _record
from ._docstring import function_doc, method_doc, property_doc


class DocGap:
    """One member that renders with a name-derived docstring.

    Attributes
    ----------
    component : str
        The object the member belongs to.
    kind : str
        ``"property"``, ``"record field"``, ``"method"`` or ``"function"``.
    name : str
        The member's name.
    where : str
        Where jm would read the text from, in the author's own terms -- the
        struct and field to comment, or the manifest key to set.
    """

    __slots__ = ("component", "kind", "name", "where")

    def __init__(self, component: str, kind: str, name: str, where: str):
        self.component = component
        self.kind = kind
        self.name = name
        self.where = where

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DocGap({self.component}.{self.name}, {self.kind})"


def _doc_blocks(root: Path, comp: str) -> dict:
    """The sacred header's blocks for *comp*, or ``{}`` when it has none."""
    from ._object import _load_doc_blocks

    return _load_doc_blocks(root, comp) or {}


def _module_doc_blocks(root: Path, module: str) -> dict:
    """A module header's blocks for its free functions, keyed by bare name."""
    from ._object import _load_module_doc_blocks

    return _load_module_doc_blocks(root, module) or {}


def gaps(root: Path, cfg: dict, only: str = "") -> list[DocGap]:
    """Every member of *cfg* whose docstring is just its name.

    Parameters
    ----------
    root : Path
        The project root, read for each component's sacred header.
    cfg : dict
        The loaded manifest.
    only : str, optional
        Restrict to one component. Empty walks them all.

    Returns
    -------
    list of DocGap
        In manifest order, so a reader can work down the list and down the
        header together.
    """
    out: list[DocGap] = []
    for comp in C.components(cfg):
        if only and comp != only:
            continue
        blocks = _doc_blocks(root, comp)
        for prop in C.properties(cfg, comp):
            _, is_stub = property_doc(comp, prop, blocks)
            if is_stub:
                name = str(prop.get("name") or "")
                out.append(
                    DocGap(
                        comp,
                        "property",
                        name,
                        f"{comp}_state_t.{name}'s own `/**< ... */`, "
                        f"or `doc =` on the property",
                    )
                )
        for method in C.methods(cfg, comp):
            _, is_stub = method_doc(comp, method, blocks)
            if is_stub:
                mname = str(method.get("name") or "")
                out.append(
                    DocGap(
                        comp,
                        "method",
                        mname,
                        f"`@brief` above {C.method_c_symbol(comp, method)}() "
                        f"in the sacred header, or `doc =` on the method",
                    )
                )
            struct = _record.c_struct(method)
            for field in _record.fields(method, blocks):
                if field.doc:
                    continue
                out.append(
                    DocGap(
                        comp,
                        "record field",
                        f"{method.get('name')}.{field.name}",
                        (
                            f"{struct}.{field.name}'s own `/**< ... */`"
                            if struct
                            else "`doc =` on the result field"
                        ),
                    )
                )
    for module in C.modules(cfg):
        if only and module != only:
            continue
        fn_blocks = _module_doc_blocks(root, module)
        for fn in C.module_functions(cfg, module):
            _, is_stub = function_doc(fn, fn_blocks)
            if is_stub:
                fname = str(fn.get("name") or "")
                out.append(
                    DocGap(
                        module,
                        "function",
                        fname,
                        f"`@brief` above {fname}() in the module header, "
                        f"or `doc =` on the function",
                    )
                )
    return out


def report(found: list[DocGap]) -> str:
    """The text `jm status --docs` prints.

    Empty input says so rather than printing nothing: a silent command reads
    as one that did not run, which is how a check gets believed for the wrong
    reason.

    Examples
    --------
    >>> print(report([]))
    docs: every member carries a docstring.
    >>> print(report([DocGap("psd", "property", "nfft", "psd_state_t.nfft")]))
    docs: 1 member documented only by its name.
    <BLANKLINE>
      psd.nfft (property)
          reads from: psd_state_t.nfft
    """
    if not found:
        return "docs: every member carries a docstring."
    lines = [
        f"docs: {len(found)} member"
        f"{'s' if len(found) != 1 else ''} documented only by "
        f"{'their' if len(found) != 1 else 'its'} name.",
        "",
    ]
    for gap in found:
        lines.append(f"  {gap.component}.{gap.name} ({gap.kind})")
        lines.append(f"      reads from: {gap.where}")
    return "\n".join(lines)
