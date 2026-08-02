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
import sys
from pathlib import Path

from . import _config as C
from . import _gluedoc
from . import _render as R

# PyMethodDef / PyGetSetDef doc field is the 4th element (0-based index 3) in
# both ``{name, meth, flags, DOC}`` and ``{name, get, set, DOC, closure}``.
_DOC_FIELD = 3

_METHODS_RE = re.compile(r"static\s+PyMethodDef\s+\w+\s*\[\s*\]\s*=\s*\{")
_GETSET_RE = re.compile(r"static\s+PyGetSetDef\s+\w+\s*\[\s*\]\s*=\s*\{")
_TP_DOC_RE = re.compile(r"\.tp_doc\s*=\s*")
_TYPE_RE = re.compile(r"static\s+PyTypeObject\s+\w+\s*=\s*\{")
# The PyTypeObject slot each array kind is wired into. A freshly spliced array
# is inert until the type points at it (gh-627).
_ARRAY_SLOT = {"PyMethodDef": "tp_methods", "PyGetSetDef": "tp_getset"}


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


# jm's generated method doc always opens with a synopsis line — ``step(x) ->
# float``, ``configure(up, dn) -> None``. It is the one part of the shape that
# has been stable across every jm version that emitted these literals.
_SYNOPSIS_RE = re.compile(r"^\w+\([^)]*\)(?:\s*->\s*\S.*)?$")


def _head(field_text: str) -> list[str]:
    """The first two non-blank logical lines of a doc field's literals.

    For a generated method doc that is the synopsis and the summary — the two
    lines jm derives and a reader would recognise.
    """
    text = "".join(_STR_LIT_RE.findall(field_text))
    lines = [ln.strip() for ln in text.split("\\n") if ln.strip()]
    return lines[:2]


def _is_jm_shaped(cur: str, der: str, fb: str | None) -> bool:
    """True when *cur*'s synopsis **and** summary are still jm's own.

    The scaffold-form comparison in :func:`_refresh_slot` asks "is this
    byte-for-byte the text *today's* jm would scaffold?" — which is
    version-sensitive by construction, and that turned out to be a real defect
    rather than a conservative choice (gh-703). A fragment generated by an
    earlier release holds text matching neither today's derived form nor
    today's fallback, so it was classified hand-written and its docs froze
    **permanently**. Every doc improvement jm shipped after sacred fragments
    existed was therefore invisible to every existing project: the only way to
    pick one up was to delete the fragment and lose the hand-written C it
    exists to protect.

    Two lines are checked, and the second one is load-bearing. The synopsis
    (``name(args) -> ret``) is the version-independent anchor — jm has always
    emitted it first — but it is **not sufficient on its own**: a downstream
    that hand-writes a richer summary typically keeps jm's synopsis line above
    it, which is exactly the RateConverter case the original gating was built
    for (``tests/test_apply_fragment_docs.py``). Matching on the synopsis alone
    silently clobbers that prose.

    So the summary must also still be one jm would produce — either the
    header-derived one or the no-Doxygen fallback. That is what separates "an
    older jm wrote this from the same header" (reclaim) from "a human wrote
    this under jm's signature line" (preserve). Everything below those two
    lines is jm's rendering and may be rebuilt freely.

    Deliberately narrow: it requires *der* to carry a synopsis at all, which
    leaves the prose-only slots (``reset``, the glue methods, ``tp_doc``,
    getset entries) on the strict scaffold-equality rule. Those have no
    version-stable anchor, so widening them would be guessing.
    """
    if not der:
        return False
    der_head = _head(der)
    if not der_head or not _SYNOPSIS_RE.match(der_head[0]):
        return False
    cur_head = _head(cur)
    if not cur_head or cur_head[0] != der_head[0]:
        return False  # different synopsis -> not this member's generated doc
    fb_head = _head(fb) if fb is not None else []
    return cur_head == der_head or (bool(fb_head) and cur_head == fb_head)


def _is_reclaimable_glue(name: str, cur: str, der: str) -> bool:
    """True when *name* is jm-owned glue whose slot still holds jm's old text.

    gh-707. For a glue slot the derived and fallback renders are **identical**
    by construction — ``_gluedoc`` never consults ``doc_blocks``, so rendering
    the fragment with the header and without it produces the same text. That
    defeats every other branch of :func:`_refresh_slot`: it is not "already
    current", not equal to the scaffold form, carries no synopsis for
    :func:`_is_jm_shaped`, and cannot satisfy the empty-slot rule because that
    one requires ``nder != nfb`` — a test for *header*-derived content, which
    jm-authored prose can never pass. So a glue slot could never be refreshed
    at all, and doppler measured 394 of them still carrying pre-gh-647
    one-liners (or, for ``__enter__``, nothing).

    The licence to be more permissive here is that these methods have **no
    authoring path**: a downstream cannot document ``state_bytes`` with
    Doxygen, because there is no declaration to attach a comment to. `der ==
    fb` on a glue slot therefore means "jm owns this text outright", not
    "there is nothing to say".

    Still not unconditional. The reclaim is limited to a slot that is empty or
    a **single logical line**, because every pre-gh-647 glue doc was a
    one-liner and every gh-647 one is multi-paragraph. That covers the whole
    reported population while bounding the worst case to "somebody hand-wrote
    a *one-line* glue docstring" — the least valuable thing to preserve. A
    rich hand-written glue doc has more than one line and is left alone.
    """
    if name not in _gluedoc.glue_method_names() or not der:
        return False
    body = "".join(_STR_LIT_RE.findall(cur))
    lines = [ln for ln in body.split("\\n") if ln.strip()]
    return len(lines) <= 1


def _refresh_slot(
    cur: str, der: str | None, fb: str | None, name: str = ""
) -> str | None:
    """Decide a slot's new doc text, or ``None`` to leave it untouched.

    *cur* / *der* / *fb* are the existing, header-derived, and scaffold-form
    (no-Doxygen) field texts. A slot is refreshed to *der* when it is safe —
    when *cur* still holds the untouched scaffold form, when *cur* opens with
    the synopsis line jm derives for it (see :func:`_is_jm_shaped`), or when it
    is empty and the derived form carries real Doxygen content. A hand-written
    doc — *cur* matching none of those — is preserved.
    """
    if der is None:
        return None
    ncur, nder = _norm(cur), _norm(der)
    if nder == ncur:
        return None  # already current (or both scaffold) — nothing to do
    nfb = _norm(fb) if fb is not None else None
    if nfb is not None and ncur == nfb:
        return der  # stale scaffold text -> refresh to derived
    if _is_jm_shaped(cur, der, fb):
        return der  # jm's own output from some earlier release -> refresh
    if _is_reclaimable_glue(name, cur, der):
        return der  # jm-owned glue still holding jm's old one-liner
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
                cur, ref[2] if ref else None, fb[2] if fb else None, name
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


_METH_FLAGS_RE = re.compile(r"METH_[A-Z_]+(?:\s*\|\s*METH_[A-Z_]+)*")
_PYARG_FMT_RE = re.compile(r'PyArg_Parse\w*\s*\((?:[^;{}]|\n)*?"([^"]*)"')


def _method_signatures(text: str) -> dict:
    """Map each ``PyMethodDef`` name to its binding's *calling signature*.

    The fingerprint is the pair that decides what Python may pass: the
    ``METH_*`` flags on the row (``METH_NOARGS`` vs ``METH_VARARGS`` vs
    ``METH_VARARGS | METH_KEYWORDS``) and the ``PyArg_Parse*`` format string
    inside the wrapper (its arity and types). Both are jm-generated; the body
    around them is the user's and is deliberately *not* compared, so a
    hand-written implementation never reads as drift.

    Scoped to methods: a property getter takes no arguments by construction,
    so there is no signature for a manifest change to invalidate.
    """
    from ._object import _extract_c_function_bodies

    mask = _code_mask(text)
    m = _METHODS_RE.search(mask)
    if m is None:
        return {}
    open_idx = m.end() - 1
    close_idx = _match_brace(mask, open_idx)
    if close_idx == -1:
        return {}
    funcs = _extract_c_function_bodies(text)
    out: dict = {}
    for s, e in _entry_spans(mask, open_idx + 1, close_idx):
        name = _entry_name(text, mask, s, e)
        if name is None:
            continue
        flags = _METH_FLAGS_RE.search(_code_mask(text[s : e + 1]))
        fmt = ""
        for fn in _row_fn_names(text, mask, (s, e)):
            body = funcs.get(fn)
            if body:
                fmt_m = _PYARG_FMT_RE.search(_code_mask(body))
                if fmt_m:
                    # Mask offsets line up with the real text.
                    fmt = body[fmt_m.start(1) : fmt_m.end(1)]
                break
        out[name] = (flags.group(0).replace(" ", "") if flags else "", fmt)
    return out


def warn_signature_drift(rel, existing: str, reference: str) -> list:
    """Warn when a fragment's binding no longer matches the manifest (gh-622).

    A sacred ``_ext_<obj>.c`` fragment only ever *gains* members on apply — an
    existing member's binding is never revised. So when a manifest edit or a
    jm upgrade changes a method's generated signature, the ``.pyi`` moves and
    the binding does not, and nothing reports it: ``jm status --check``
    compares manifest-owned files, and both artifacts legitimately match what
    jm would write. The reporter found 26 such methods across 10 doppler
    modules only by building them and calling each one.

    Re-rendering is not available here — the bodies are the user's — so this
    says exactly what diverged and what to do about it, the same trade gh-609
    made for a hand-edited ``impl`` body. Returns the drifted names (for
    tests); emits nothing when they agree, which is the overwhelmingly common
    case.
    """
    ex = _method_signatures(existing)
    ref = _method_signatures(reference)
    drifted = [n for n, sig in ref.items() if n in ex and ex[n] != sig]
    if not drifted:
        return []

    def _show(sig):
        return (sig[0] or "METH_?") + (f' "{sig[1]}"' if sig[1] else "")

    detail = "; ".join(
        f"{n}: binding {_show(ex[n])} vs manifest {_show(ref[n])}"
        for n in drifted
    )
    print(
        f"warning: {rel}: binding signature no longer matches the manifest"
        f" [{detail}]. A sacred fragment only gains missing members on apply,"
        " so a changed signature stays as written while the .pyi moves — the"
        " stub now documents a call the extension will reject. Delete"
        f" {rel} and re-run `just-makeit apply` to regenerate it (any"
        " hand-written body in it is lost), or edit the binding to match.",
        file=sys.stderr,
    )
    return drifted


def _splice_first_array(
    existing: str,
    reference: str,
    ref_mask: str,
    ref_decl_start: int,
    ref_close: int,
    fn_names: list[str],
    ref_funcs: dict,
) -> str:
    """Splice a whole binding array *existing* does not have yet (gh-627).

    The zero-to-one case. :func:`transplant_missing_bindings` adds rows to an
    array that is already there; when the object had no property at all its
    fragment has no ``PyGetSetDef`` to add a row to, so the declaration was
    skipped entirely — silently, which is the damage: the `.pyi` still gained
    the property, so the stub advertised a member the extension did not define
    and ``jm status --check`` stayed green (see gh-622).

    Three pieces have to land together, or the result is worse than doing
    nothing: the wrapper function(s), the array itself, and the
    ``.tp_getset``/``.tp_methods`` slot pointing the type at it — an array no
    type references is dead code that compiles and changes nothing.

    Returns *existing* unchanged when the fragment has no ``PyTypeObject`` to
    wire into (a hand-written shape jm does not recognise), leaving such a
    fragment to the delete-and-regenerate path rather than half-editing it.
    """
    end = ref_close + 1
    if end < len(reference) and reference[end] == ";":
        end += 1
    array_text = reference[ref_decl_start:end] + "\n\n"
    decl = reference[ref_decl_start : ref_decl_start + 80]
    kind = "PyGetSetDef" if "PyGetSetDef" in decl else "PyMethodDef"
    slot = _ARRAY_SLOT[kind]
    name_m = re.search(r"static\s+\w+\s+(\w+)\s*\[", array_text)
    if name_m is None:
        return existing
    array_name = name_m.group(1)

    ex_mask = _code_mask(existing)
    type_m = _TYPE_RE.search(ex_mask)
    if type_m is None:
        return existing
    type_open = type_m.end() - 1
    type_close = _match_brace(ex_mask, type_open)
    if type_close == -1:
        return existing

    from ._object import _extract_c_function_bodies

    have = _extract_c_function_bodies(existing)
    funcs_text = "\n\n".join(
        ref_funcs[n] for n in fn_names if n in ref_funcs and n not in have
    )
    if funcs_text:
        funcs_text += "\n\n"

    out = existing
    # Right-to-left: the slot sits inside the type object, which starts after
    # the insertion point for the array, so editing it first keeps
    # type_m.start() valid for the second splice.
    if f".{slot}" not in ex_mask[type_open:type_close]:
        indent = "    "
        out = (
            out[:type_close]
            + f"{indent}.{slot} = {array_name},\n"
            + out[type_close:]
        )
    out = (
        out[: type_m.start()] + funcs_text + array_text + out[type_m.start() :]
    )
    return out


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

    When *existing* has no array of a given kind at all (an object's very
    first property), there is no sentinel to splice a row against, so
    :func:`_splice_first_array` inserts the whole array plus the type-object
    slot instead (gh-627). v1 skipped that case, which was silent rather than
    inert: the `.pyi` gained the property while the binding did not, so the
    stub advertised a member the extension never defined.
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
            # gh-627: *existing* has no array of this kind — the object's very
            # first property. Splice the whole array (v1 skipped this, and the
            # binding silently never gained the member while the .pyi did).
            out = _splice_first_array(
                out,
                reference,
                ref_mask,
                ref_m.start(),
                ref_close,
                missing_fn_names,
                ref_funcs,
            )
            continue
        open_idx = decl_m.end() - 1
        close_idx = _match_brace(ex_mask, open_idx)
        if close_idx == -1:
            continue
        sent = re.search(r"\{\s*NULL", ex_mask[open_idx:close_idx])
        rows_at = open_idx + sent.start() if sent else open_idx + 1
        rows_text = "".join(f"    {r},\n" for r in missing_rows)
        # gh-544: a row may bind a name to a wrapper the fragment ALREADY
        # defines — that is exactly what a destructor alias is
        # (``{"close", ...}`` and ``{"destroy", ...}`` both point at
        # ``<Obj>_destroy``). Splicing the function in again is a C
        # redefinition error, so only genuinely new wrappers are inserted;
        # the row itself is still added.
        _existing_funcs = _extract_c_function_bodies(out)
        funcs_text = "\n\n".join(
            ref_funcs[n]
            for n in missing_fn_names
            if n in ref_funcs and n not in _existing_funcs
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
            # gh-622: the splice above is additive by name, so a member whose
            # *signature* changed keeps its old binding while the .pyi takes
            # the new one. Nothing can re-render it here (the body is the
            # user's), so report it — after the splice, so a member that was
            # just added correctly is not also flagged.
            warn_signature_drift(
                frag.relative_to(root) if frag.is_absolute() else frag,
                updated,
                reference,
            )
            # gh-541: the teardown wrappers are manifest-derived, not
            # hand-owned, once [<obj>.destroy] exists. transplant_missing_
            # bindings above is additive by name, so it adds the alias ROW but
            # leaves a pre-existing `<Obj>_destroy`/`<Obj>_exit` body swallowing
            # the status — the exact silent revert this feature exists to
            # prevent. Overwrite those two from the reference. Scoped to
            # declaring objects, so every other fragment is untouched.
            if C.destroy_spec(cfg, ctx["component"]):
                _w = ctx["ComponentW"]
                _ref_funcs = O._extract_c_function_bodies(reference)
                _own = {
                    n: _ref_funcs[n]
                    for n in (f"{_w}_destroy", f"{_w}_exit")
                    if n in _ref_funcs
                }
                if _own:
                    updated = O._restore_c_function_bodies(updated, _own)
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
