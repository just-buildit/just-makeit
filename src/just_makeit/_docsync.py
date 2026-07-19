"""_docsync.py — refresh runtime ``__doc__`` in per-object binding fragments.

jm derives numpy docstrings from each sacred ``<obj>_core.h`` Doxygen and
writes them into the ``.pyi`` stubs on every ``jm apply`` (0.14.6). The C
**runtime** docstrings — the ``PyMethodDef`` doc slot, the ``PyGetSetDef`` doc
field, and ``tp_doc`` — live instead in the per-object
``native/src/<mod>/<mod>_ext_<obj>.c`` fragments, which are *sacred* and never
re-rendered by apply (to protect hand-written bindings). So a header Doxygen
edit reaches the stub yet ``help(Obj.method)`` keeps the stale scaffold
fallback.

jm 0.14.8/0.14.9 closed that gap by re-rendering the whole fragment from the
manifest; that silently dropped hand-written bindings the manifest can't
express (custom getters, list accessors, bespoke constructors) and was reverted
in 0.14.10. This module takes the safe route: render a *reference* fragment in
memory (the form jm would generate, carrying the derived docs) and transplant
**only the docstring string-literals** into the existing fragment, matched by
entry name. Every function body and every binding whose name is not in the
reference (i.e. every hand-written non-manifest binding) is left byte-for-byte
identical — preservation is a structural guarantee, not a body-splice.

The transplant edits three slots, all keyed by the Python name jm already
emits:

* ``PyMethodDef <X>_methods[]`` — field index 3 (``{name, meth, flags, DOC}``).
* ``PyGetSetDef <X>_getset[]``  — field index 3 (``{name, get, set, DOC, clo}``).
* ``.tp_doc = <literal>,`` in the ``PyTypeObject``.

Idempotent: once a slot equals the reference, the replacement is an identity and
the file is left untouched, so a second ``jm apply`` produces no diff.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import _config as C
from . import _render as R

# PyMethodDef / PyGetSetDef doc field is the 4th element (0-based index 3) in
# both ``{name, meth, flags, DOC}`` and ``{name, get, set, DOC, closure}``.
_DOC_FIELD = 3

_METHODS_RE = re.compile(r"static\s+PyMethodDef\s+\w+\s*\[\s*\]\s*=\s*\{")
_GETSET_RE = re.compile(r"static\s+PyGetSetDef\s+\w+\s*\[\s*\]\s*=\s*\{")
_TP_DOC_RE = re.compile(r"\.tp_doc\s*=\s*")


def _code_mask(text: str) -> str:
    """Return *text* with string/char-literal contents and comments blanked.

    Same length as *text*. Characters inside ``"..."`` / ``'...'`` literals and
    inside ``//`` / ``/* */`` comments become spaces (newlines kept), while the
    delimiting quotes themselves survive. Structural scans (braces, commas) run
    on the mask so punctuation hidden inside strings or comments is invisible;
    content is sliced from the original at offsets the mask reveals.
    """
    out: list[str] = []
    i, n = 0, len(text)
    NORMAL, STR, CHAR, LINE, BLOCK = range(5)
    st = NORMAL
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if st == NORMAL:
            if c == '"':
                out.append('"')
                st = STR
            elif c == "'":
                out.append("'")
                st = CHAR
            elif c == "/" and nxt == "/":
                out.append("  ")
                i += 2
                st = LINE
                continue
            elif c == "/" and nxt == "*":
                out.append("  ")
                i += 2
                st = BLOCK
                continue
            else:
                out.append(c)
            i += 1
        elif st in (STR, CHAR):
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if (st == STR and c == '"') or (st == CHAR and c == "'"):
                out.append(c)
                st = NORMAL
            else:
                out.append(" ")
            i += 1
        elif st == LINE:
            if c == "\n":
                out.append("\n")
                st = NORMAL
            else:
                out.append(" ")
            i += 1
        else:  # BLOCK
            if c == "*" and nxt == "/":
                out.append("  ")
                i += 2
                st = NORMAL
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
    return "".join(out)


def _match_brace(mask: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at *open_idx* in *mask* (or -1)."""
    depth = 0
    for i in range(open_idx, len(mask)):
        ch = mask[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _entry_spans(mask: str, body_start: int, body_end: int) -> list[tuple]:
    """Top-level ``{...}`` entry spans within an initializer-list body.

    *body_start* / *body_end* bound the inner text (exclusive of the array's
    own outer braces). Each returned ``(start, end)`` brackets one entry from
    its ``{`` to its matching ``}`` inclusive.
    """
    spans: list[tuple] = []
    i = body_start
    while i < body_end:
        if mask[i] == "{":
            end = _match_brace(mask, i)
            if end == -1 or end > body_end:
                break
            spans.append((i, end))
            i = end + 1
        else:
            i += 1
    return spans


def _field_spans(mask: str, start: int, end: int) -> list[tuple]:
    """Spans of the top-level comma-separated fields inside one entry.

    *start* / *end* are the entry's ``{`` and ``}`` indices. Commas nested in
    parens/brackets/braces (or hidden in strings/comments via the mask) do not
    split. Each ``(s, e)`` excludes the surrounding commas/braces.
    """
    fields: list[tuple] = []
    depth = 0
    fs = start + 1
    for i in range(start + 1, end):
        ch = mask[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            fields.append((fs, i))
            fs = i + 1
    fields.append((fs, end))
    return fields


def _entry_name(text: str, mask: str, start: int, end: int) -> str | None:
    """First string literal inside an entry, as plain text (or ``None``)."""
    q1 = mask.find('"', start, end)
    if q1 == -1:
        return None
    q2 = mask.find('"', q1 + 1, end)
    if q2 == -1:
        return None
    return text[q1 + 1 : q2]


def _doc_slots(text: str, mask: str, array_re: re.Pattern) -> dict[str, tuple]:
    """Map entry name -> ``(field_start, field_end, field_text)`` for an array.

    Locates the first ``static PyMethodDef``/``PyGetSetDef`` array *array_re*
    matches, then for every entry that has a name and a doc field returns the
    absolute span and current text of that doc field. Sentinel ``{NULL}`` rows
    (no name) are skipped.
    """
    m = array_re.search(mask)
    if not m:
        return {}
    open_idx = m.end() - 1  # the '{' the regex ends on
    close_idx = _match_brace(mask, open_idx)
    if close_idx == -1:
        return {}
    out: dict[str, tuple] = {}
    for s, e in _entry_spans(mask, open_idx + 1, close_idx):
        name = _entry_name(text, mask, s, e)
        if name is None:
            continue
        fields = _field_spans(mask, s, e)
        if len(fields) <= _DOC_FIELD:
            continue
        fs, fe = fields[_DOC_FIELD]
        out[name] = (fs, fe, text[fs:fe])
    return out


def _tp_doc_span(text: str, mask: str) -> tuple | None:
    """Span and text of the ``.tp_doc`` value, up to its terminating comma."""
    m = _TP_DOC_RE.search(mask)
    if not m:
        return None
    start = m.end()
    depth = 0
    for i in range(start, len(mask)):
        ch = mask[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            return (start, i, text[start:i])
    return None


_STR_LIT_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _norm(field_text: str) -> str:
    """Normalize a doc-field's *content* for format-tolerant comparison.

    Concatenates the field's C string-literal contents and drops escapes and
    whitespace, so ``"DDC type."`` and ``"DDC type.\\n"`` compare equal and a
    ``NULL`` slot (no literals) normalizes to the empty string. This lets the
    refresh recognise a scaffold-form slot regardless of cosmetic newline /
    indentation drift between the jm version that generated the fragment and
    the one running now.
    """
    text = "".join(_STR_LIT_RE.findall(field_text))
    return re.sub(r"\s+", "", text.replace("\\n", "").replace("\\t", ""))


def _refresh_slot(cur: str, der: str | None, fb: str | None) -> str | None:
    """Decide a slot's new doc text, or ``None`` to leave it untouched.

    *cur* / *der* / *fb* are the existing, header-derived, and scaffold-form
    (no-Doxygen) field texts. A slot is refreshed to *der* only when it is safe
    — i.e. when *cur* still holds the untouched scaffold form, or is empty and
    the derived form carries real Doxygen content. A hand-written doc (``cur``
    matching neither scaffold nor derived) is preserved.
    """
    if der is None:
        return None
    ncur, nder = _norm(cur), _norm(der)
    if nder == ncur:
        return None  # already current (or both scaffold) — nothing to do
    nfb = _norm(fb) if fb is not None else None
    if nfb is not None and ncur == nfb:
        return der  # stale scaffold text -> refresh to derived
    if ncur == "" and nfb is not None and nder != nfb:
        return der  # empty slot -> fill, but only with real Doxygen content
    return None  # hand-written -> preserve


def transplant_docs(existing: str, reference: str, fallback: str) -> str:
    """Return *existing* with refreshable doc slots updated from *reference*.

    For each ``PyMethodDef`` / ``PyGetSetDef`` entry whose name appears in both
    *existing* and *reference*, and for ``tp_doc``, the slot is refreshed to
    the *reference* (header-derived) text **only** when it still holds the
    scaffold form — determined by *fallback*, the same fragment rendered with
    the header Doxygen ignored — or is empty with real derived content.
    Hand-written docstrings and every non-manifest binding pass through
    untouched. Edits apply right-to-left so earlier spans stay valid.
    """
    ex_mask = _code_mask(existing)
    ref_mask = _code_mask(reference)
    fb_mask = _code_mask(fallback)

    edits: list[tuple] = []  # (start, end, new_text)
    for array_re in (_METHODS_RE, _GETSET_RE):
        ex_slots = _doc_slots(existing, ex_mask, array_re)
        ref_slots = _doc_slots(reference, ref_mask, array_re)
        fb_slots = _doc_slots(fallback, fb_mask, array_re)
        for name, (fs, fe, cur) in ex_slots.items():
            ref = ref_slots.get(name)
            fb = fb_slots.get(name)
            new_text = _refresh_slot(
                cur, ref[2] if ref else None, fb[2] if fb else None
            )
            if new_text is not None:
                edits.append((fs, fe, new_text))

    ex_tp = _tp_doc_span(existing, ex_mask)
    ref_tp = _tp_doc_span(reference, ref_mask)
    fb_tp = _tp_doc_span(fallback, fb_mask)
    if ex_tp:
        new_tp = _refresh_slot(
            ex_tp[2],
            ref_tp[2] if ref_tp else None,
            fb_tp[2] if fb_tp else None,
        )
        if new_tp is not None:
            edits.append((ex_tp[0], ex_tp[1], new_tp))

    if not edits:
        return existing
    out = existing
    for start, end, new_text in sorted(edits, reverse=True):
        out = out[:start] + new_text + out[end:]
    return out


def transplant_state_triplet(
    existing: str, c_funcs: list[str], pmd_rows: str
) -> str:
    """Inject the serializable state triplet into a sacred fragment (gh-404).

    When a `serializable` object's per-object `_ext_<obj>.c` fragment is
    hand-owned (created once, never regenerated), the `state_bytes`/`get_state`/
    `set_state` wrappers + ``PyMethodDef`` rows are missing.  Inject them:
    *c_funcs* (the three wrapper bodies) before the ``static PyMethodDef``
    array, and *pmd_rows* before the array's ``{NULL}`` sentinel.

    Idempotent: if the array already has a ``"state_bytes"`` entry, return
    *existing* unchanged.  Hand-written bindings are never touched — only the
    two insertions are made.  Both *c_funcs*/*pmd_rows* come from
    :func:`_context._methods.serializable_triplet_parts`, so the injected glue
    is byte-identical to the regenerate path.
    """
    mask = _code_mask(existing)
    m = _METHODS_RE.search(mask)
    if not m:
        return existing
    open_brace = m.end() - 1
    close_brace = _match_brace(mask, open_brace)
    if close_brace == -1:
        return existing
    # Idempotent: the triplet row is present iff "state_bytes" appears as an
    # entry-name string inside the array body (quotes survive in the source).
    if '"state_bytes"' in existing[open_brace:close_brace]:
        return existing
    # Insert rows before the {NULL ...} sentinel (scan the mask so a brace
    # hidden in a string/comment can't be mistaken for it).
    sent = re.search(r"\{\s*NULL", mask[open_brace:close_brace])
    rows_at = open_brace + sent.start() if sent else open_brace + 1
    funcs_text = "\n\n".join(c_funcs) + "\n\n"
    # Apply right-to-left so the earlier (funcs) offset stays valid.
    out = existing[:rows_at] + pmd_rows + existing[rows_at:]
    out = out[: m.start()] + funcs_text + out[m.start() :]
    return out


_ROW_FN_RE = re.compile(r"(\w+)\s*$")


def _array_names(
    text: str, mask: str, array_re: re.Pattern
) -> dict[str, tuple[int, int]]:
    """entry name -> (start, end) span for every named entry in the first
    *array_re* match in *text* (``{}`` if the array itself is absent)."""
    m = array_re.search(mask)
    if not m:
        return {}
    open_idx = m.end() - 1
    close_idx = _match_brace(mask, open_idx)
    if close_idx == -1:
        return {}
    names: dict[str, tuple[int, int]] = {}
    for s, e in _entry_spans(mask, open_idx + 1, close_idx):
        name = _entry_name(text, mask, s, e)
        if name is not None:
            names[name] = (s, e)
    return names


def _row_fn_names(
    text: str, mask: str, entry_span: tuple[int, int]
) -> list[str]:
    """Function-pointer identifiers referenced by an entry's non-name
    fields: field[1] for a ``PyMethodDef`` row, fields[1:3] (getter and
    setter) for a ``PyGetSetDef`` row. A ``NULL`` setter (read-only
    property) contributes nothing."""
    s, e = entry_span
    names: list[str] = []
    for fs, fe in _field_spans(mask, s, e)[1:3]:
        field_text = text[fs:fe].strip()
        if field_text in ("NULL", "0"):
            continue
        m = _ROW_FN_RE.search(field_text)
        if m:
            names.append(m.group(1))
    return names


def transplant_missing_bindings(existing: str, reference: str) -> str:
    """Additively splice manifest-derived methods/properties missing from
    *existing* in from *reference* (gh-440).

    For each of ``PyMethodDef``/``PyGetSetDef``, an entry present in
    *reference* but absent (by name) from *existing* is a genuinely new
    binding — its wrapper function(s) (extracted from *reference* by name,
    brace-matched) are inserted before the ``static`` array declaration, and
    its row before the array's ``{NULL ...}`` sentinel. An entry already
    present in *existing* (hand-patched or not) is never touched, matching
    :func:`transplant_state_triplet`'s own idempotence.

    v1 = additive only: if *existing* has no array of a given kind at all
    (e.g. an object's very first property), there is no sentinel/decl to
    splice against, and that array kind is left alone -- delete-and-adopt
    is still needed to go from zero properties to one. Every other case
    (new method or property on an object that already has at least one of
    that kind) is spliced without touching anything else in the fragment.
    """
    from ._object import _extract_c_function_bodies

    ref_funcs = _extract_c_function_bodies(reference)
    ref_mask = _code_mask(reference)
    out = existing
    for array_re in (_METHODS_RE, _GETSET_RE):
        ex_mask = _code_mask(out)
        ex_names = _array_names(out, ex_mask, array_re)
        ref_m = array_re.search(ref_mask)
        if ref_m is None:
            continue
        ref_open = ref_m.end() - 1
        ref_close = _match_brace(ref_mask, ref_open)
        if ref_close == -1:
            continue
        missing_rows: list[str] = []
        missing_fn_names: list[str] = []
        for s, e in _entry_spans(ref_mask, ref_open + 1, ref_close):
            name = _entry_name(reference, ref_mask, s, e)
            if name is None or name in ex_names:
                continue
            missing_rows.append(reference[s : e + 1])
            for fn in _row_fn_names(reference, ref_mask, (s, e)):
                if fn not in missing_fn_names:
                    missing_fn_names.append(fn)
        if not missing_rows:
            continue
        decl_m = array_re.search(ex_mask)
        if decl_m is None:
            continue  # no array of this kind in *existing* -- v1 skip
        open_idx = decl_m.end() - 1
        close_idx = _match_brace(ex_mask, open_idx)
        if close_idx == -1:
            continue
        sent = re.search(r"\{\s*NULL", ex_mask[open_idx:close_idx])
        rows_at = open_idx + sent.start() if sent else open_idx + 1
        rows_text = "".join(f"    {r},\n" for r in missing_rows)
        funcs_text = "\n\n".join(
            ref_funcs[n] for n in missing_fn_names if n in ref_funcs
        )
        if funcs_text:
            funcs_text += "\n\n"
        # Right-to-left: the rows offset always sits after the decl offset,
        # so inserting there first leaves decl_m.start() valid for the
        # second splice.
        out = out[:rows_at] + rows_text + out[rows_at:]
        out = out[: decl_m.start()] + funcs_text + out[decl_m.start() :]
    return out


def refresh_module_fragment_docs(
    root: Path, cfg: dict, *, only_mod: str | None = None
) -> list[Path]:
    """Refresh runtime ``__doc__`` in every module's per-object fragments.

    For each generate-able module object, render the reference fragment in
    memory and transplant its doc slots into the on-disk
    ``<mod>_ext_<obj>.c``. Hand-written ``*_extra.c`` files are never touched
    (they are only ``#include``d). Returns the fragment paths that changed.
    """
    from . import _object as O

    pkg = C.project_name(cfg)
    changed: list[Path] = []
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        if only_mod is not None and mod != only_mod:
            continue
        ext_dir = root / "native" / "src" / mod
        derived = O.build_component_ctxs(root, cfg, mod, pkg)
        # gh-504: key by frag_id, not component — a view shares its parent's
        # `component` but owns a distinct fragment (`<mod>_ext_<frag_id>.c`), so
        # keying by component would collapse the view onto the parent and
        # transplant the view's docs into the parent's fragment.
        fallback = {
            c.get("frag_id", c["component"]): c
            for c in O.build_component_ctxs(
                root, cfg, mod, pkg, force_fallback=True
            )
        }
        for ctx in derived:
            comp = ctx.get("frag_id", ctx["component"])
            frag = ext_dir / f"{mod}_ext_{comp}.c"
            if not frag.exists():
                continue
            existing = frag.read_text(encoding="utf-8")
            reference = R.render_module_ext_fragment(ctx)
            fb = R.render_module_ext_fragment(fallback[comp])
            updated = transplant_docs(existing, reference, fb)
            # gh-440: a new method/property added to the manifest since this
            # fragment was last generated is missing entirely -- splice it in
            # additively rather than requiring delete-and-recreate.
            updated = transplant_missing_bindings(updated, reference)
            # gh-404: a serializable object whose sacred fragment predates the
            # flag (or was hand-written) lacks the state triplet — inject it.
            # (Usually already covered by the general splice above, since the
            # triplet is part of the same rendered reference; kept as a
            # redundant, idempotent safety net.)
            if C.is_serializable(cfg, comp):
                from ._context._methods import serializable_triplet_parts

                wp = (
                    f"{ctx['Component']}Obj"
                    if C.is_no_state(cfg, comp)
                    else ctx["Component"]
                )
                c_funcs, pmd, _ = serializable_triplet_parts(
                    comp, ctx["Component"], wp
                )
                updated = transplant_state_triplet(updated, c_funcs, pmd)
            if updated != existing:
                frag.write_text(updated, encoding="utf-8")
                changed.append(frag)
    return changed
