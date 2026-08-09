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
from . import _gluedoc
from . import _record
from . import _report
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


#: jm's generic `tp_doc`, from `_object.py`'s ``[f"{Component} type."]``
#: else-branch, matched against `_norm`'s whitespace-stripped form (so the
#: space before ``type`` is gone by the time this runs). Anchored end to end:
#: a docstring that merely *opens* this way carries prose underneath that a
#: human wrote, and this predicate is a licence to overwrite.
_GENERIC_TP_DOC_RE = re.compile(r"^\w+type\.$")


def _is_generic_tp_doc(cur: str) -> bool:
    """True when *cur* is jm's own placeholder class docstring.

    The `tp_doc` counterpart of :func:`_is_reclaimable_glue`, and it rests on
    the same argument: there is nothing of the author's here to protect. jm
    emits ``"<Component> type."`` precisely when it had no header ``@brief``
    and no declaration to build a class block from, so a fragment still
    carrying it has never had a real class docstring at all.

    *cur* is a raw field slot — C string literals, quotes included — because
    that is what `_tp_doc_span` yields and what `_norm` knows how to read.

    Examples
    --------
    >>> _is_generic_tp_doc('"W type.\\\\n"')
    True
    >>> _is_generic_tp_doc('"Resampler type."')
    True
    >>> _is_generic_tp_doc('"W type.\\\\n" "\\\\n" "Hand-written prose.\\\\n"')
    False
    >>> _is_generic_tp_doc('"Resamples a signal.\\\\n"')
    False
    >>> _is_generic_tp_doc("NULL")
    False
    """
    return bool(_GENERIC_TP_DOC_RE.match(_norm(cur)))


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

    gh-871: **unconditional**, where it used to stop at a slot that was empty
    or a single logical line.

    That bound was drawn for gh-707's reported population, which was entirely
    pre-gh-647 one-liners, and it read as a cheap safety margin: "a rich
    hand-written glue doc has more than one line and is left alone." What it
    actually did was freeze the feature shut. *Every* gh-647 glue docstring is
    multi-paragraph, so the moment a project picked one up its glue prose
    became unrevisable by any later jm — and the three fixes that followed all
    landed in that dead zone:

    ===========  ==========================================================
    issue        what could not reach an existing fragment
    ===========  ==========================================================
    gh-805 §H    ``__exit__`` saying "finalizing" instead of "releasing"
    gh-864       ``destroy`` naming the inherited exception class
    gh-869       the ``Raises`` section itself
    ===========  ==========================================================

    Each rendered correctly into a *fresh* fragment and into the ``.pyi``, so
    every test passed while the runtime face of every existing module object
    stayed wrong. `tests/test_body_vs_doc_gate.py` caught it only once it
    started reading the ``PyMethodDef`` literal.

    The licence is the paragraph above, applied honestly: there is **no
    authoring path** for these members. A downstream cannot document
    ``__exit__`` with Doxygen — there is no declaration to attach a comment
    to — so `der == fb` on a glue slot means jm owns the text outright. That
    is true of a five-paragraph glue docstring exactly as much as a one-line
    one; line count was never evidence of authorship, only a proxy for "how
    much would we destroy if we were wrong".

    So the reclaim is now total and, in exchange, **loud**: every reclaimed
    member is reported by name (see `refresh_module_fragment_docs`), the way
    `refresh_glue_bindings` already reports a repaired arity. A hand-edited
    glue docstring is no longer silently preserved *or* silently overwritten —
    it is overwritten and named, and `git diff` on a sacred fragment is a
    thing this workflow already expects a human to read.
    """
    return name in _gluedoc.glue_method_names() and bool(der)


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


def transplant_docs(
    existing: str,
    reference: str,
    fallback: str,
    reclaimed: "list[str] | None" = None,
) -> str:
    """Return *existing* with refreshable doc slots updated from *reference*.

    For each ``PyMethodDef`` / ``PyGetSetDef`` entry whose name appears in both
    *existing* and *reference*, and for ``tp_doc``, the slot is refreshed to
    the *reference* (header-derived) text **only** when it still holds the
    scaffold form — determined by *fallback*, the same fragment rendered with
    the header Doxygen ignored — or is empty with real derived content.
    Hand-written docstrings and every non-manifest binding pass through
    untouched. Edits apply right-to-left so earlier spans stay valid.

    Parameters
    ----------
    reclaimed : list of str, optional
        Sink for the names of **glue** members whose prose was overwritten
        (gh-871). Since that reclaim became unconditional it can now replace a
        hand-edited glue docstring, so the caller reports what it took rather
        than doing it silently — the trade the issue's decision rests on. Left
        ``None`` by callers that only want the text.
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
                # gh-871: only glue is reported. Every other refresh already
                # required the slot to hold jm's own scaffold or synopsis, so
                # nothing a human wrote was at stake there.
                if (
                    reclaimed is not None
                    and name in _gluedoc.glue_method_names()
                    and _norm(cur)
                ):
                    reclaimed.append(name)

    ex_tp = _tp_doc_span(existing, ex_mask)
    ref_tp = _tp_doc_span(reference, ref_mask)
    fb_tp = _tp_doc_span(fallback, fb_mask)
    if ex_tp:
        new_tp = _refresh_slot(
            ex_tp[2],
            ref_tp[2] if ref_tp else None,
            fb_tp[2] if fb_tp else None,
        )
        # gh-805 §F: `tp_doc` is one of the prose-only slots `_is_jm_shaped`
        # deliberately leaves on strict scaffold equality — it has no synopsis
        # line to anchor on. That rule silently froze this slot the moment the
        # *fallback* moved: declaring `create_error` on an existing object made
        # jm render the full class block in both the derived and the scaffold
        # form, so the fragment's `"<Component> type."` matched neither and was
        # classified hand-written. The `.pyi` gained a `Raises` section that
        # `help(Obj)` did not, and `jm status` reported up to date — the exact
        # gh-871 shape, one slot over.
        #
        # `"<Component> type."` is not a hand-written docstring. It is jm's own
        # literal from `_object.py`'s else-branch, emitted when there was
        # nothing to build a block from, and matching it costs a downstream
        # only a one-line summary jm wrote itself.
        if new_tp is None and _is_generic_tp_doc(ex_tp[2]) and ref_tp:
            if _norm(ref_tp[2]) != _norm(ex_tp[2]):
                new_tp = ref_tp[2]
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


def _row_bodies(text: str) -> dict:
    """Map each ``PyMethodDef`` name to ``(wrapper body, row span)``.

    The row's first function field that resolves to a real definition wins.
    Shared by the calling-convention and return-shape fingerprints below so
    both walk the table the same way and cannot disagree about which body
    belongs to which member.
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
        body = ""
        for fn in _row_fn_names(text, mask, (s, e)):
            if funcs.get(fn):
                body = funcs[fn]
                break
        out[name] = (body, (s, e))
    return out


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
    out: dict = {}
    for name, (body, (s, e)) in _row_bodies(text).items():
        flags = _METH_FLAGS_RE.search(_code_mask(text[s : e + 1]))
        fmt = ""
        if body:
            fmt_m = _PYARG_FMT_RE.search(_code_mask(body))
            if fmt_m:
                # Mask offsets line up with the real text.
                fmt = body[fmt_m.start(1) : fmt_m.end(1)]
        out[name] = (flags.group(0).replace(" ", "") if flags else "", fmt)
    return out


#: The jm-emitted constructs that decide what a binding hands back, and the
#: manifest key each one is the signature of. Measured from the rendered
#: fragment for every shape rather than assumed:
#:
#: ===================== ==========================================
#: marker                emitted for
#: ===================== ==========================================
#: ``Py_RETURN_NONE``    ``status_return`` — the int is status only
#: ``PyErr_Format``      ``status_return`` / ``error_negative``
#: ``PyStructSequence_`` ``single`` — one record, by value
#: ``PyArray_NewFrom``   ``record_dtype`` — a structured ndarray
#: ``PyList_New``        the list-of-records shape
#: ===================== ==========================================
#:
#: Each marker lists the spellings that satisfy it, because the fragment being
#: checked may be **hand-written**: a body returning ``Py_None`` after its own
#: ``Py_INCREF`` implements the same shape as the ``Py_RETURN_NONE`` macro jm
#: emits, and flagging it would be a false positive on correct code.
_RETURN_SHAPE_MARKERS = {
    "Py_RETURN_NONE": ("Py_RETURN_NONE", "Py_None"),
    "PyStructSequence_New": ("PyStructSequence_New",),
    "PyArray_NewFromDescr": (
        "PyArray_NewFromDescr",
        "PyArray_SimpleNewFromDescr",
    ),
    "PyList_New": ("PyList_New",),
}

#: The error-translation axis, kept separate because it cannot be a plain
#: substring test. It is what separates ``error_negative`` from a plain scalar
#: return — both end in ``PyLong_FromLong``, and only the raise branch differs
#: — but **every** wrapper already carries one ``PyErr_SetString`` for the
#: liveness guard, so testing for that spelling would match everything and
#: neuter the axis. A body raises if it uses a spelling the guard does not, or
#: uses the guard's spelling more than once.
_RAISE_MARKER = "raises"
_RAISE_SPELLINGS = ("PyErr_Format", "PyErr_SetObject", "PyErr_SetFromErrno")


def _raises(code: str) -> bool:
    """Whether a masked wrapper body translates a failure into an exception."""
    return any(s in code for s in _RAISE_SPELLINGS) or (
        code.count("PyErr_SetString") > 1
    )


def _method_return_shapes(text: str) -> dict:
    """Map each ``PyMethodDef`` name to the return-shape markers it exhibits.

    Presence of a construct, not its surrounding code — so a hand-written
    wrapper that genuinely implements the shape carries the same marker as the
    generated one and never reads as drift. Comment and string contents are
    masked out, so a marker named in a docstring does not count.
    """
    out: dict = {}
    for name, (body, _span) in _row_bodies(text).items():
        code = _code_mask(body) if body else ""
        found = {
            label
            for label, spellings in _RETURN_SHAPE_MARKERS.items()
            if any(s in code for s in spellings)
        }
        if _raises(code):
            found.add(_RAISE_MARKER)
        out[name] = frozenset(found)
    return out


def signature_drift_details(existing: str, reference: str) -> "dict[str, str]":
    """``{member: why it differs}`` for members the reference has moved past.

    Extracted from `warn_signature_drift` so `jm status` can ask the same
    question without emitting a warning (gh-848). One comparison with two
    consumers rather than a second copy of the rules — the peer-implementation
    trap this repo keeps paying for.

    **The direction is the classification.** A member the reference declares
    and the fragment lacks is a codegen or manifest change the fragment has not
    received. A member the fragment has and the reference does not is the
    author's body doing more than jm would, which is the entire point of a
    sacred fragment. Only the first is returned, and that asymmetry is what
    lets `status` split "a fix you are not receiving" from "you wrote it that
    way" — the two things gh-848 could not tell apart in a bare path list.

    Returns
    -------
    dict of str to str
        Member name to a one-line explanation. Empty when the fragment is
        merely the author's, which is the overwhelmingly common case.
    """
    ex = _method_signatures(existing)
    ref = _method_signatures(reference)

    def _show(sig):
        return (sig[0] or "METH_?") + (f' "{sig[1]}"' if sig[1] else "")

    details: dict = {}
    for n, sig in ref.items():
        if n in ex and ex[n] != sig:
            details[n] = (
                f"{n}: binding {_show(ex[n])} vs manifest {_show(sig)}"
            )

    # gh-815: the calling convention is only one of the two ways a member can
    # stop matching the manifest. `status_return`, `error_negative`, `single`
    # and `record_dtype` all leave METH flags and the PyArg format *identical*
    # and change what comes back, so they sail past the comparison above —
    # while the .pyi, regenerated from the same manifest, moves.
    ex_shape = _method_return_shapes(existing)
    ref_shape = _method_return_shapes(reference)
    for n, markers in ref_shape.items():
        missing = markers - ex_shape.get(n, frozenset())
        if not missing:
            continue
        want = ", ".join(sorted(missing))
        note = f"{n}: the manifest's result shape needs {want}, absent here"
        details[n] = f"{details[n]}; {note}" if n in details else note
    return details


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
    details = signature_drift_details(existing, reference)

    if not details:
        return []
    drifted = sorted(details)
    _report.warn(
        f"{rel}: binding no longer matches the manifest"
        f" [{'; '.join(details[n] for n in drifted)}]. A sacred fragment only"
        " gains missing members on apply, so a changed member stays as"
        " written while the .pyi moves — the stub now documents behaviour the"
        f" extension does not have. Delete {rel} and re-run"
        " `just-makeit apply` to regenerate it (any hand-written body in it is"
        " lost), or edit the binding to match.",
        # Advisory: `jm status` reports these fragments as unreconciled and
        # says outright they are not counted as drift, because no jm command
        # clears them — the remaining difference is one only the author can
        # settle.
        gates=False,
    )
    return drifted


_MAX_OUT_SUFFIX = "_max_out"


def refresh_glue_bindings(existing: str, reference: str) -> tuple[str, list]:
    """Re-render the ``*_max_out`` bindings whose signature drifted (gh-767).

    :func:`warn_signature_drift` reports every member whose binding no longer
    matches what jm would emit, and reporting is the right answer for almost
    all of them: the wrapper body is the user's, so re-rendering it is a
    clobber. ``*_max_out`` is the exception, on the same licence
    :func:`_is_reclaimable_glue` takes for the glue docstrings — **there is no
    authoring path**. The bound C function lives in the sacred ``_core.c``;
    the wrapper is pure marshalling jm writes and nobody edits, because there
    is nothing in it to edit.

    That exception is exactly the population gh-761 stranded. Deriving the
    arity from the C prototype fixed what jm *emits*, but a project's existing
    fragments are frozen at whatever jm emitted when they were created, so a
    state-only ``x_max_out(state)`` kept a ``METH_NOARGS`` row while the stub
    moved to ``max_out(n)``. The stub then documents a call the extension
    rejects, which is a runtime ``TypeError`` per surface.

    Both halves move together or neither does: the ``METH_*`` flags on the row
    and the wrapper body that parses (or does not parse) the argument. Returns
    the updated text and the member names repaired.
    """
    from ._object import _extract_c_function_bodies

    ex_sig = _method_signatures(existing)
    ref_sig = _method_signatures(reference)
    names = [
        n
        for n, sig in ref_sig.items()
        if n.endswith(_MAX_OUT_SUFFIX) and n in ex_sig and ex_sig[n] != sig
    ]
    if not names:
        return existing, []

    out = existing
    ref_funcs = _extract_c_function_bodies(reference)
    repaired: list[str] = []
    for name in names:
        out_mask = _code_mask(out)
        ref_mask = _code_mask(reference)
        ex_rows = _array_names(out, out_mask, _METHODS_RE)
        ref_rows = _array_names(reference, ref_mask, _METHODS_RE)
        if name not in ex_rows or name not in ref_rows:
            continue
        # The wrapper is replaced whole — signature included — by a direct
        # substring swap rather than through _restore_c_function_bodies.
        # That function bails when the two signatures differ (gh-267), and
        # here they differ *by design*: going from METH_NOARGS to
        # METH_VARARGS is exactly a change from
        # `(self, PyObject *Py_UNUSED(ignored))` to `(self, PyObject *args)`.
        # Moving the row's flags without the body is the one outcome worse
        # than leaving both alone — the call stops raising TypeError and
        # starts silently ignoring the argument, returning the state-only
        # answer for a length-bearing query.
        ex_funcs = _extract_c_function_bodies(out)
        swapped = True
        for f in _row_fn_names(reference, ref_mask, ref_rows[name]):
            if f in ref_funcs and f in ex_funcs:
                out = out.replace(ex_funcs[f], ref_funcs[f], 1)
            elif f in ref_funcs:
                swapped = False
        if not swapped:
            continue
        out_mask = _code_mask(out)
        ex_rows = _array_names(out, out_mask, _METHODS_RE)
        if name not in ex_rows:
            continue
        s, e = ex_rows[name]
        rs, re_ = ref_rows[name]
        # The whole row, not just the flags field. The doc slot has to move
        # with them or the runtime ends up self-contradictory: the binding
        # accepts `ptr_max_out(4)` while `help()` still shows the old
        # `ptr_max_out()` synopsis. transplant_docs cannot fix that on its
        # own — it reads a changed synopsis as "somebody hand-wrote this" and
        # preserves it. For a `*_max_out` there is nobody to have written it;
        # the text comes from _gluedoc, not from the header's Doxygen.
        out = out[:s] + reference[rs : re_ + 1] + out[e + 1 :]
        repaired.append(name)
    return out, repaired


_KWLIST_RE = re.compile(
    r"static\s+char\s*\*\s*kwlist\s*\[\s*\]\s*=\s*\{([^}]*)\}"
)


def _init_body(text: str) -> str | None:
    """The constructor wrapper's body, or ``None``.

    The wrapper prefix is read off the file rather than rebuilt from the
    manifest: it is ``<Component>Obj`` for some objects and ``<Component>``
    for others, and a fragment naming its type something else entirely (a
    view, a hand-written shape) still has exactly one ``_init``.
    """
    from ._object import _extract_c_function_bodies

    bodies = _extract_c_function_bodies(text)
    return next((b for n, b in bodies.items() if n.endswith("_init")), None)


# In a PyArg format a character is not one-to-one with a parameter: jm emits
# `O&` for a path (the converter form) and `y#` for bytes — two characters,
# one parameter each. `|` and `$` are partition markers, not parameters.
# Counting characters would put the `|` boundary in the wrong place for any
# constructor taking a path or a bytes param, and where the `|` falls is the
# entire question here.
_FMT_NON_PARAM = "&#|$"


def _fmt_param_count(fmt: str) -> int:
    """How many parameters a PyArg format segment describes."""
    return sum(1 for ch in fmt if ch not in _FMT_NON_PARAM)


def _init_kwarg_optionality(text: str) -> tuple | None:
    """``(required names, optional names)`` split at the constructor's ``|``.

    ``None`` when the fragment has no constructor, no kwlist or no format
    string to read — the same "nothing to compare" answer `_init_kwargs`
    gives, so a caller can treat both the same way.

    This is the axis `_init_kwargs` cannot see. It returns names in order and
    nothing else, so a parameter that *gains a default* without moving
    produces identical names in identical order and a moved ``|`` — no drift
    by that comparison, while the stub gains a ``= …`` the binding does not
    honour (gh-823).
    """
    names = _init_kwargs(text)
    body = _init_body(text)
    if not names or body is None:
        return None
    m = _PYARG_FMT_RE.search(_code_mask(body))
    if not m:
        return None
    fmt = body[m.start(1) : m.end(1)]
    if "|" not in fmt:
        return (names, ())
    head, _, _tail = fmt.partition("|")
    k = _fmt_param_count(head)
    return (names[:k], names[k:])


def _init_kwargs(text: str) -> tuple[str, ...]:
    """The constructor's keyword names, in ``PyArg_ParseTupleAndKeywords``
    order. Empty when the fragment has no ``*_init`` or no kwlist.
    """
    body = _init_body(text)
    if not body:
        return ()
    m = _KWLIST_RE.search(_code_mask(body))
    if not m:
        return ()
    lit = body[m.start(1) : m.end(1)]
    return tuple(n for n in re.findall(r'"([^"]*)"', lit))


def warn_init_kwargs_drift(rel, existing: str, reference: str):
    """Warn when a refresh would change the constructor's keyword arguments.

    doppler#616 named this class, and it is invisible to every member-level
    audit: regeneration can rewrite ``kwlist[]`` while losing no member at
    all, so ``Obj(bank=…)`` becomes a ``TypeError`` and ``Obj(a, b)`` binds
    its positionals to different parameters — silently, since both spellings
    still compile.

    Reported rather than repaired, and the asymmetry with
    :func:`refresh_glue_bindings` is deliberate. ``<Obj>_init`` is in
    ``_restore_c_function_bodies``'s always-regenerate set so that a newly
    declared state field or output buffer is never silently dropped from the
    constructor. Preserving the old ``kwlist`` under a freshly rendered body
    would leave the name array and the ``&var`` argument list out of step —
    ``PyArg_ParseTupleAndKeywords`` would then bind each keyword to the
    *neighbouring* variable. That is worse than either honest outcome: it
    compiles, it runs, and it puts the caller's values in the wrong fields.

    So jm says exactly what changed and leaves the decision with the author.
    Returns ``(added, removed, reordered)`` for tests; emits nothing when the
    two agree.
    """
    added, removed, reordered, detail = init_kwargs_drift(existing, reference)
    if not detail:
        return ((), (), False)
    _report.warn(
        f"{rel}: refreshing this fragment would change the"
        f" constructor's keyword arguments [{detail}]."
        " The kwlist is regenerated with the body it belongs to, so jm will"
        " not preserve it on its own — a kwlist kept under a fresh body binds"
        " each keyword to the wrong variable. Reconcile the manifest with the"
        " binding, or keep the hand-written constructor in an _extra.c.",
        # Gating: gh-823 made this condition reach `drift_count`, so
        # `jm status --check` fails on it. This is the warning that printed
        # correctly for months inside a block of advisory ones.
        gates=True,
    )
    return (added, removed, reordered)


def init_kwargs_drift(existing: str, reference: str):
    """``(added, removed, reordered, detail)`` for the constructor's keyword
    arguments, comparing a fragment on disk against a fresh render.

    Split out of :func:`warn_init_kwargs_drift` so the comparison has one
    implementation and two presentations: that function prints to stderr
    during a refresh, and ``jm status`` renders it into a report section
    (gh-612). Two copies of this would drift in the usual way — one taught
    about a new kwlist spelling, the other quietly still wrong.

    *detail* is the human-readable summary and is ``""`` exactly when the two
    agree, so it doubles as the "is there drift" predicate.
    """
    ex = _init_kwargs(existing)
    ref = _init_kwargs(reference)
    if not ex or not ref:
        return ((), (), False, "")

    added = tuple(n for n in ref if n not in ex)
    removed = tuple(n for n in ex if n not in ref)
    reordered = ex != ref and not added and not removed
    detail = []
    if removed:
        detail.append("no longer accepted: " + ", ".join(removed))
    if added:
        detail.append("newly accepted: " + ", ".join(added))
    if reordered:
        detail.append(
            "same names, new positional order: "
            f"{'/'.join(ex)} -> {'/'.join(ref)}"
        )

    # gh-823: the third axis, and the one that is silent on its own. The
    # names above answer "which keywords" and "in what order"; neither sees
    # the PyArg `|`. A parameter that gains a default in the manifest without
    # moving produces identical names in identical order and a moved `|` — so
    # the comparison returned "no drift" while the regenerated .pyi grew a
    # `= …` the fragment's binding does not honour, and the published
    # constructor raised when called as documented.
    #
    # Compared by NAME rather than by position: on this class the reordering
    # IS the drift, so a positional comparison names the wrong parameter —
    # doppler's reported the one that still worked and stayed silent about
    # the one that did not.
    ex_opt, ref_opt = (
        _init_kwarg_optionality(existing),
        _init_kwarg_optionality(reference),
    )
    if ex_opt is not None and ref_opt is not None:
        became_optional = tuple(
            n for n in ref_opt[1] if n in ex_opt[0] and n in ref
        )
        became_required = tuple(
            n for n in ref_opt[0] if n in ex_opt[1] and n in ref
        )
        if became_optional:
            detail.append(
                "now omittable, still required here: "
                + ", ".join(became_optional)
            )
        if became_required:
            detail.append(
                "now required, still omittable here: "
                + ", ".join(became_required)
            )
    return (added, removed, reordered, "; ".join(detail))


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
    _new = [n for n in fn_names if n in ref_funcs and n not in have]
    # gh-779: the same file-scope carry the row-splicing path does. There are
    # two splice paths, and fixing only the one the reporter's tree happened
    # to take is how this class of bug keeps returning — doppler's fragment
    # already had a `PyGetSetDef` so it went through the other branch, while
    # an object gaining its *first* property comes through here. The
    # declaration a wrapper references has to travel on both, or "it compiles
    # for me" is a fact about which branch ran.
    # By name, not by text — see the matching note in
    # `transplant_missing_bindings`. The declaration already in *existing* has
    # been through the project's formatter; the one in *reference* has not.
    _deps: list[str] = []
    _have = set(_file_scope_decls(existing))
    for _n in _new:
        for _name, _decl in _referenced_file_scope_decls(
            reference, ref_funcs[_n]
        ):
            if _name not in _have:
                _deps.append(_decl)
                _have.add(_name)
    funcs_text = "\n\n".join([*_deps, *(ref_funcs[n] for n in _new)])
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


_IDENT_RE = re.compile(r"\b[A-Za-z_]\w*\b")
# A file-scope initialised static: `static const char *const NAME[] = {…};`,
# `static PyTypeObject *NAME = NULL;`. Anchored at column 0 so a static local
# inside a function body (always indented in generated C) is not claimed.
_FILE_SCOPE_RE = re.compile(
    r"^static\s+[^\n;=]*?\b(\w+)\s*(?:\[[^\]]*\])?\s*=", re.M
)


def _file_scope_decls(text: str) -> "dict[str, str]":
    """``{name: full declaration text}`` for each file-scope static in *text*.

    The declaration runs to its terminating ``;``, counting braces so an
    initialiser list is captured whole. Comment- and string-masked first, so a
    ``static`` inside a doc literal is not mistaken for a declaration.
    """
    mask = _code_mask(text)
    out: dict[str, str] = {}
    for m in _FILE_SCOPE_RE.finditer(mask):
        i, depth = m.end(), 0
        while i < len(mask):
            c = mask[i]
            if c in "{[(":
                depth += 1
            elif c in "}])":
                depth -= 1
            elif c == ";" and depth == 0:
                break
            i += 1
        if i < len(mask):
            out.setdefault(m.group(1), text[m.start() : i + 1])
    return out


def _referenced_file_scope_decls(
    reference: str, body: str
) -> "list[tuple[str, str]]":
    """``(name, declaration)`` for each file-scope static *body* references.

    The registration-free half of the gh-779 fix: rather than asking "is this
    one of the referent types jm knows about?", ask which identifiers the
    wrapper actually names and whether the reference render declares any of
    them. A new kind of file-scope dependency is then carried the first time
    it exists, instead of the first time somebody notices it does not compile.

    Returns the **name** alongside the text because that is what callers must
    dedupe on. Asking whether the declaration's text is already in the target
    is a formatting-sensitive test, and these fragments are routinely
    reformatted after every apply: a GNU-indented initialiser and a K&R one
    declare the same symbol and never match as substrings, so the guard fails
    open and the carry emits a redefinition. Same class as gh-770 — a text
    comparison standing in for an identity comparison.
    """
    decls = _file_scope_decls(reference)
    if not decls:
        return []
    names = set(_IDENT_RE.findall(_code_mask(body)))
    return [(n, d) for n, d in decls.items() if n in names]


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
        _new_fns = [
            n
            for n in missing_fn_names
            if n in ref_funcs and n not in _existing_funcs
        ]
        # gh-729/gh-779: a wrapper can depend on file-scope statics, which are
        # not functions and so are invisible to the name-based extraction
        # above. A full render prepends them to the function; this path
        # dropped them, and the fragment gained a body referencing symbols
        # nothing declared — it does not compile.
        #
        # gh-729 fixed the one referent type it had (a record's
        # `<fn>_type`/`<fn>_desc`) with a finder that knows that shape by
        # name. gh-779 is the second type — an `enum =` property's
        # `_enum_<Class>_<prop>[]` table — arriving by the identical route,
        # and the reporter's read is the right one: *the per-type split is
        # the bug*, not either instance of it. A third referent would fail
        # the same way and nobody would know until it did not compile.
        #
        # So ask what the body actually references and carry whatever
        # `reference` declares at file scope for it. The record finder stays
        # because its three declarations must travel as one block, but it is
        # no longer the only thing standing between a spliced wrapper and a
        # missing symbol.
        # Deduped by declared NAME, never by declaration text. `out` has been
        # through the project's formatter and `reference` has not, so the two
        # spellings of one declaration never match as substrings and a text
        # guard fails open — emitting a second copy and a redefinition error.
        # gh-770 in a new costume, and reachable through the workflow gh-777
        # now prescribes: the gate says "run apply", and apply is what breaks
        # the build.
        _have = set(_file_scope_decls(out))
        _preludes: list[str] = []
        for _n in _new_fns:
            _desc = _record.find_descriptor(reference, _n)
            # The structseq triple travels as one block, so one of its names
            # standing for all three is enough to know it is already there.
            if _desc and f"{_n}_type" not in _have:
                _preludes.append(_desc)
                _have.update(_file_scope_decls(_desc))
            # gh-788: the structured-dtype cache and its builder are the same
            # kind of coupled block, and invisible to the generic
            # reference-following below for the reason `find_dtype` records.
            _dt = _record.find_dtype(reference, _n)
            if _dt and f"{_n}_dtype" not in _have:
                _preludes.append(_dt)
                _have.update(_file_scope_decls(_dt))
            for _name, _decl in _referenced_file_scope_decls(
                reference, ref_funcs[_n]
            ):
                if _name not in _have:
                    _preludes.append(_decl)
                    _have.add(_name)
        funcs_text = "\n\n".join(
            [*_preludes, *(ref_funcs[n] for n in _new_fns)]
        )
        if funcs_text:
            funcs_text += "\n\n"
        # Right-to-left: the rows offset always sits after the decl offset,
        # so inserting there first leaves decl_m.start() valid for the
        # second splice.
        out = out[:rows_at] + rows_text + out[rows_at:]
        out = out[: decl_m.start()] + funcs_text + out[decl_m.start() :]
    return out


def _leading_comment_start(text: str, start: int) -> int:
    """Offset of the block comment immediately above *start*, or *start*.

    A hand-written binding's comment is the part that says *why* it is
    hand-written; carrying the function without it strands the explanation.
    Only a ``/* … */`` block that ends on the line directly above (no blank
    line between) is claimed — a comment separated by a blank line belongs to
    the file, not to this function.
    """
    head = text[:start]
    stripped = head.rstrip(" \t\n")
    if not stripped.endswith("*/"):
        return start
    # Exactly one newline may separate the comment's end from the function.
    if head[len(stripped) :].count("\n") != 1:
        return start
    open_at = stripped.rfind("/*")
    if open_at == -1 or "*/" in stripped[open_at + 2 : len(stripped) - 2]:
        return start
    line_start = text.rfind("\n", 0, open_at) + 1
    return line_start if not text[line_start:open_at].strip() else open_at


def absent_members(existing: str, reference: str) -> "list[str]":
    """Members *reference* binds that *existing* does not (gh-777).

    The reconcilable half of a fragment difference, and the half the
    ``unreconciled`` bucket should never have swallowed. That bucket exists
    for a wrapper **body** that differs — reformatted, or hand-tuned — which
    is the author's and which no jm command clears, so tolerating it is
    right. A missing ``PyMethodDef``/``PyGetSetDef`` row is categorically
    different: it is generated wiring that has gone, and
    :func:`transplant_missing_bindings` already knows how to put it back.

    Tolerating it meant a project could carry a member its ``.pyi``
    advertises and its extension does not define, indefinitely, with CI
    green — which is the exact shape gh-622 and gh-767 were filed to end, and
    how doppler accumulated 58 arity mismatches without a red build.

    Row *names* only, deliberately. Comparing anything about the bodies would
    drag the tolerated case back in, and the question here is solely "is
    something jm generates simply not there?".
    """
    ex_mask, ref_mask = _code_mask(existing), _code_mask(reference)
    out: list[str] = []
    for array_re in (_METHODS_RE, _GETSET_RE):
        have = set(_array_names(existing, ex_mask, array_re))
        for name in _array_names(reference, ref_mask, array_re):
            if name not in have:
                out.append(name)
    return out


def binds_functions(text: str) -> bool:
    """True when *text* clearly defines functions, judged without the parser.

    The independent second opinion for :func:`extraction_failed`. Derived from
    the binding arrays — brace-matching over a comment/string-masked copy —
    which shares no code with `_extract_c_function_bodies`'s
    ``<type>\\n<name>(`` header regex. A style change that defeats one is
    unlikely to defeat the other in the same way, which is the entire point of
    asking twice.

    A row in ``PyMethodDef``/``PyGetSetDef`` names a function pointer, so a
    file with rows has functions by construction.
    """
    mask = _code_mask(text)
    for array_re in (_METHODS_RE, _GETSET_RE):
        for span in _array_names(text, mask, array_re).values():
            if _row_fn_names(text, mask, span):
                return True
    return False


def extraction_failed(text: str) -> bool:
    """True when the body parser found nothing in a fragment that plainly has
    functions — i.e. the parse failed rather than the file being empty.

    doppler raised this on gh-770's PR, and it is the difference between
    closing an instance and closing the class. `_extract_c_function_bodies`
    returns ``{}`` both for "there is nothing here" and for "I could not read
    this", and every caller reads the second as the first — which is the whole
    mechanism of gh-770: GNU style writes ``name (``, extraction returned
    ``{}``, and the caller took that as licence to overwrite the file.

    Tolerating one more spelling does not fix that. The gap between the
    styles jm anticipates and the styles a formatter will produce is open by
    construction: a downstream tracking ``pre-commit autoupdate`` has no fixed
    input style at all, so a future release that wraps a long signature across
    the ``(`` puts extraction back to ``{}`` and the same silent overwrite
    returns.

    "I found no functions in a file that obviously contains functions" is
    information, and it is the one signal that survives a style nobody has
    thought of yet — including the ones nobody can enumerate now.
    """
    return bool(text.strip()) and binds_functions(text)


def transplant_hand_written(
    existing: str, reference: str, drop_members: "frozenset[str]" = frozenset()
) -> str:
    """Carry *existing*'s hand-written bindings into the fresh *reference*.

    The C counterpart of the ``.pyi``'s ``# jm:hand`` append path (gh-538),
    and the missing half of :func:`~._object._restore_c_function_bodies`.
    That function replaces a rendered body with the hand-edited one **when
    both sides have the name** — so a hand *edit* to a generated wrapper
    survives, while a hand *addition* jm never generates has nothing to match
    against and is dropped on the floor. Both are hand-written C in a file
    whose own header promises "Hand-patches to this file are preserved across
    jm commands"; only one of them was.

    Two things travel, and they have to travel together or the result does not
    compile: the function definition itself (plus the block comment directly
    above it) and the ``PyMethodDef``/``PyGetSetDef`` row that binds it to a
    Python name. A carried function with no row is dead code that warns as
    unused; a carried row with no function is a link error.

    A row is carried when its *name* is absent from the reference array, which
    also covers the case where a hand-written alias points at a wrapper jm
    does generate. Definitions go in ahead of the first binding array, rows
    ahead of the array's ``{NULL …}`` sentinel, so both land in the file's
    normal order.

    Deliberately not covered: a hand-written *file-scope* declaration that is
    not a function — a static lookup table, a typedef, a helper macro. Those
    are invisible to the name-based extraction this is built on, so a fragment
    whose hand-written binding depends on one still needs the ``_extra.c``
    escape hatch. Named in gh-770 rather than left in this docstring.
    """
    from ._object import _extract_c_function_bodies

    ex_funcs = _extract_c_function_bodies(existing)
    if not ex_funcs:
        return reference
    ref_funcs = _extract_c_function_bodies(reference)
    orphans = [n for n in ex_funcs if n not in ref_funcs]

    def _dropped(member: str) -> bool:
        """*member* is what this command just deleted, or derived from it.

        The suffix match covers the satellites jm generates alongside a
        method — ``tune`` takes ``tune_max_out`` with it. Without this,
        `jm remove --method` would delete the method from the manifest and
        the `.pyi` while the binding it carried lived on forever, which is
        the mirror image of the bug this function exists to fix.
        """
        return any(
            member == d or member.startswith(f"{d}_") for d in drop_members
        )

    out = reference
    ex_mask = _code_mask(existing)
    # Wrappers reachable only from a row this command removed. They are
    # orphans by the same test as a hand-written function — absent from the
    # fresh render — so without collecting them they would be carried back in
    # and left as unreferenced dead code.
    dropped_fns: set[str] = set()
    for array_re in (_METHODS_RE, _GETSET_RE):
        out_mask = _code_mask(out)
        ref_names = _array_names(out, out_mask, array_re)
        rows = []
        for name, (s, e) in _array_names(existing, ex_mask, array_re).items():
            if name in ref_names:
                continue
            if _dropped(name):
                dropped_fns.update(_row_fn_names(existing, ex_mask, (s, e)))
                continue
            rows.append(existing[s : e + 1])
        if not rows:
            continue
        decl_m = array_re.search(out_mask)
        if decl_m is None:
            # The reference has no array of this kind to hang the row on.
            # Splicing one in would also need the PyTypeObject slot wired up;
            # leave it to _splice_first_array's path rather than half-doing it.
            continue
        open_idx = decl_m.end() - 1
        close_idx = _match_brace(out_mask, open_idx)
        if close_idx == -1:
            continue
        sent = re.search(r"\{\s*NULL", out_mask[open_idx:close_idx])
        rows_at = open_idx + sent.start() if sent else open_idx + 1
        out = (
            out[:rows_at]
            + "".join(f"    {r},\n" for r in rows)
            + out[rows_at:]
        )

    orphans = [n for n in orphans if n not in dropped_fns]
    if orphans:
        anchor = min(
            (
                m.start()
                for m in (
                    pat.search(_code_mask(out))
                    for pat in (_METHODS_RE, _GETSET_RE, _TYPE_RE)
                )
                if m is not None
            ),
            default=-1,
        )
        blocks = []
        for name in orphans:
            body = ex_funcs[name]
            at = existing.index(body)
            blocks.append(
                existing[_leading_comment_start(existing, at) : at + len(body)]
            )
        text = "\n\n".join(blocks) + "\n\n"
        out = (
            out + "\n\n" + text
            if anchor == -1
            else out[:anchor] + text + out[anchor:]
        )
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
            _reclaimed: list[str] = []
            updated = transplant_docs(existing, reference, fb, _reclaimed)
            # gh-440: a new method/property added to the manifest since this
            # fragment was last generated is missing entirely -- splice it in
            # additively rather than requiring delete-and-recreate.
            updated = transplant_missing_bindings(updated, reference)
            _rel = frag.relative_to(root) if frag.is_absolute() else frag
            # gh-767: repair the one class of drifted binding that has no
            # authoring path (`*_max_out`) before reporting the rest, so a
            # member jm can and does fix is not also warned about.
            updated, _fixed = refresh_glue_bindings(updated, reference)
            for _name in _fixed:
                print(f"  update  {_rel}: {_name} binding arity")
            # gh-871: the reclaim is unconditional now, so it is also loud.
            # This is the whole safety story for overwriting a glue docstring
            # somebody may have hand-edited: it is named, in the same place a
            # repaired arity is named, and the diff is right there.
            if _reclaimed:
                print(
                    f"  update  {_rel}: refreshed jm-owned docstring(s) for "
                    f"{', '.join(sorted(set(_reclaimed)))}"
                )
            # gh-622: the splice above is additive by name, so a member whose
            # *signature* changed keeps its old binding while the .pyi takes
            # the new one. Nothing can re-render it here (the body is the
            # user's), so report it — after the splice, so a member that was
            # just added correctly is not also flagged.
            warn_signature_drift(_rel, updated, reference)
            # doppler#616: a refresh that loses no member can still rewrite
            # the constructor's kwlist. Invisible to a member-level audit, so
            # it gets its own report.
            warn_init_kwargs_drift(_rel, updated, reference)
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
