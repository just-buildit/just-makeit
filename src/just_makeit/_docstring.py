"""_docstring.py — derive Python docstrings from C header Doxygen comments.

The sacred ``<obj>_core.h`` carries hand-written Doxygen (``@brief``,
``@param``, ``@return``, plus free-text body paragraphs). jm treats that
header as the single source of truth for documentation: this module parses
those comment blocks and the binding/stub generators turn them into
numpy-style Python docstrings.

Two responsibilities:

1. :func:`extract_doc_blocks` — map each declared C function name in a header
   to the raw ``/** ... */`` block that immediately precedes it. Shared by
   ``apply`` (which re-derives on every run) and ``bind``.
2. :func:`parse_doxygen_block` — turn one raw block into a structured
   :class:`DoxyBlock` (summary, body, params, return), tolerant of partial or
   malformed tags. Returns ``None`` when the block carries no real content
   (e.g. the trivial ``@brief <name>.`` template jm itself scaffolds), so the
   generators fall back to their name-based stubs until a human writes docs.

The renderers (:func:`render_numpy_method_doc` etc.) emit only prose — the
caller owns Python type annotations and the synthesized doctest ``Examples``
block, so type mapping stays in ``_stubs``/``_context`` where it already lives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple, Sequence


def extract_doctests(text: str) -> list[str]:
    """Return every triple-quoted docstring in *text* that contains a doctest.

    Used by the doctest gate to harvest the synthesized ``Examples`` blocks
    from a generated ``.pyi`` (or any source) so they can be validated and,
    against a built extension, executed. A "doctest" here is any docstring
    containing a ``>>>`` prompt.
    """
    out: list[str] = []
    # Triple-double-quoted docstrings (what the stub generator emits).
    for m in re.finditer(r'"""(.*?)"""', text, re.DOTALL):
        body = m.group(1)
        if ">>>" in body:
            out.append(body)
    return out


# Inline markup that reduces to its argument. Both Doxygen command prefixes
# are accepted (``@p`` and ``\p`` are the same command). The trailing
# whitespace requirement is what keeps ``@parameter`` from being read as
# ``@p`` + ``arameter``.
_INLINE_TAG_RE = re.compile(r"[@\\](?:ref|[pcaeb])[ \t]+")


def _strip_doxy_inline(text: str) -> str:
    """Drop Doxygen inline reference markers, keeping their argument.

    ``@p name`` / ``@c name`` / ``@a name`` / ``@e name`` / ``@b name`` and
    ``@ref name`` are parameter/code/emphasis references that read as noise in
    a Python docstring (``"length @p code_len"`` → ``"length code_len"``).

    The marker is removed whatever follows it (gh-641). The previous form
    matched only a bare ``\\w+`` argument, so every idiomatic non-word usage
    survived verbatim into the rendered docstring — ``@c -1``, ``@c "A"``
    (doppler's BLUE-keyword idiom), ``@c +/-10^(clip_db/20)``. Doxygen's own
    rule is "mark the next *token* as code", which has never been restricted
    to identifiers.
    """
    return _INLINE_TAG_RE.sub("", text)


@dataclass
class DoxyBlock:
    """Structured contents of one Doxygen ``/** ... */`` comment.

    Attributes
    ----------
    brief : str
        One-line summary (from ``@brief``, or the lead text of a single-line
        block, or the first body line when neither is present).
    body : list of str
        Extended-description paragraphs that follow the brief and precede the
        first ``@param``/``@return`` tag. Blank-line separated.
    params : list of tuple(str, str)
        ``(name, description)`` in declaration order. Descriptions may have
        been joined from continuation lines.
    returns : str
        ``@return`` / ``@returns`` text (empty if absent).
    examples : list of str
        Verbatim interior lines of every ``@code`` / ``@endcode`` block, in
        order, ready to render as a doctest ``Examples`` section.
    param_dirs : dict
        ``{name: direction}`` for any ``@param[in]`` / ``@param[out]`` /
        ``@param[in,out]`` that carried a direction specifier (gh-650).
        Absent from the mapping when the ``@param`` had no bracket group.
    tags : list of tuple(str, str)
        ``(command, text)`` for every recognised command with no structured
        destination — ``@note``, ``@warning``, ``@see``, ``@retval``, and the
        rest. Quarantined here rather than discarded (gh-641): holding them
        keeps them out of the rendered prose today, and leaves gh-652 a
        rendering-only change when they gain numpy sections.
    """

    brief: str = ""
    body: list[str] = field(default_factory=list)
    params: list[tuple[str, str]] = field(default_factory=list)
    returns: str = ""
    examples: list[str] = field(default_factory=list)
    param_dirs: dict[str, str] = field(default_factory=dict)
    tags: list[tuple[str, str]] = field(default_factory=list)

    def param_desc(self, name: str) -> str | None:
        """Return the description for parameter *name*, or ``None``.

        Descriptions are already cleaned of Doxygen inline word-references at
        parse time (see ``_strip_doxy_inline``).
        """
        for pname, desc in self.params:
            if pname == name:
                return desc
        return None


# jm's own scaffolder emits "@param name  Initial name (default: X)." — the
# canonical shape (see _context/_state.py) a hand-edited header is expected
# to preserve or update in place (gh-442).
_HEADER_DEFAULT_RE = re.compile(r"\(default:\s*([^)]+?)\)\.?\s*$")


def header_default(desc: str | None) -> str | None:
    """Extract the trailing ``(default: X)`` value from a ``@param`` description.

    Returns ``None`` when *desc* is absent or carries no recognizable
    ``(default: ...)`` suffix — best-effort by design (gh-442): a header
    that documents its default in some other shape is silently skipped
    rather than misparsed.
    """
    if not desc:
        return None
    m = _HEADER_DEFAULT_RE.search(desc)
    return m.group(1).strip() if m else None


# A body line whose line break carries meaning: a markdown bullet or numbered
# list item, a table row, or a `@li`/`@arg` list entry. Joining these into a
# flowing paragraph destroys the structure the author wrote (gh-653).
_STRUCTURED_RE = re.compile(
    r"^(?:[-*+]\s+"  # - bullet   * bullet   + bullet
    r"|\d+[.)]\s+"  # 1. numbered   2) numbered
    r"|\|"  # | table | row |
    r"|[@\\](?:li|arg)\b)"  # @li / @arg list entry
)


# A numbered marker specifically. Bullets, tables and `@li` can only ever be
# markers; `N.` / `N)` at line-start is ambiguous, because 79-col wrapping in
# a C header lands one there whenever a sentence closes a parenthetical
# (gh-717): "…the carrier phase restart at\n0) and clears…".
_NUMBERED_RE = re.compile(r"^\d+[.)]\s")


def is_structured_line(line: str) -> bool:
    """True when *line*'s break is load-bearing and must not be re-flowed.

    Shape only — whether a *numbered* marker really starts a list also
    depends on what precedes it, which is :func:`starts_list_item`'s job.

    >>> [is_structured_line(s) for s in ("- a", "1. b", "| c |", "prose")]
    [True, True, True, False]
    """
    return bool(_STRUCTURED_RE.match(line.strip()))


def starts_list_item(stripped: str, prose: list[str], in_block: bool) -> bool:
    """True when *stripped* opens a list item here, given what came before.

    gh-717. ``is_structured_line`` answers "does this look like a marker?".
    That is enough for a bullet, a table row or a ``@li``, none of which
    occurs mid-sentence. It is *not* enough for ``N.`` / ``N)``: a C header
    wrapped to 79 columns puts a numbered marker at line-start every time a
    sentence closes a parenthetical, and jm was splitting the sentence in
    half around it --

        Zeroes both sample clocks (so `elapsed_s` and the carrier phase
        restart at
        0) and clears the resampler's delay line and fractional accumulator.

    So a numbered marker counts only where a list could actually begin: at
    the start of the body, after a blank line, immediately after a lead-in
    line ending in ``:``, or continuing a numbered run already open. Prose
    that merely happens to wrap onto a digit does none of those.

    The ``:`` lead-in clause is what keeps the common authored shape working
    without demanding a blank line the author never wrote::

        Modes:
        1. fast
        2. slow

    Parameters
    ----------
    stripped : str
        The candidate line, already stripped.
    prose : list of str
        Prose lines accumulated since the last break; empty means the
        candidate is at a paragraph boundary.
    in_block : bool
        Whether a structured block is already open.

    Examples
    --------
    >>> starts_list_item("- fast", ["Modes are:"], False)
    True
    >>> starts_list_item("1. fast", [], False)
    True
    >>> starts_list_item("1. fast", ["Modes:"], False)
    True
    >>> starts_list_item("0) and clears the delay line.", ["restart at"], False)
    False
    """
    if not is_structured_line(stripped):
        return False
    if not _NUMBERED_RE.match(stripped):
        return True  # a bullet/table/@li marker is never mid-sentence
    if in_block or not prose:
        return True
    return prose[-1].rstrip().endswith(":")


def group_paragraphs(lines: list[str]) -> list[str]:
    """Join a list of body *lines* into paragraphs, preserving structure.

    Consecutive non-blank prose lines become one space-joined paragraph; blank
    lines separate paragraphs. That is what makes multi-line Doxygen prose
    render as flowing text rather than one short line per source line.

    **Structured lines are exempt** (gh-653). A markdown bullet list, a
    numbered list, a pipe table or a ``@li``/``@arg`` entry carries meaning in
    its line breaks, and joining them produced
    ``"- fast: low quality - slow: high quality"`` — one run-on line that reads
    as neither prose nor a list. doppler measured **28** headers with bullet
    lists and **5** with tables, the lists typically enumerating modes or flags
    for an enum-valued parameter, which is exactly where a reader most needs
    the structure.

    A structured run is returned as a single paragraph with its newlines
    intact; the renderers emit any paragraph containing a newline verbatim
    rather than re-wrapping it, so the two halves cannot disagree about what
    counts as structure.

    Examples
    --------
    >>> group_paragraphs(["one", "two"])
    ['one two']
    >>> group_paragraphs(["- fast: low", "- slow: high"])
    ['- fast: low\\n- slow: high']
    >>> group_paragraphs(["Modes:", "- fast", "- slow"])
    ['Modes:', '- fast\\n- slow']
    """
    paras: list[str] = []
    prose: list[str] = []
    block: list[str] = []
    item_indent: int | None = None  # indent of the open list item, if any

    def _flush() -> None:
        nonlocal item_indent
        if prose:
            paras.append(" ".join(prose))
            prose.clear()
        if block:
            paras.append("\n".join(block))
            block.clear()
        item_indent = None

    for ln in lines:
        stripped = ln.strip()
        indent = len(ln) - len(ln.lstrip())
        if not stripped:
            _flush()
            continue
        if starts_list_item(stripped, prose, bool(block)):
            if prose:  # prose then a list: separate paragraphs
                paras.append(" ".join(prose))
                prose.clear()
            block.append(stripped)
            item_indent = indent
        elif block and item_indent is not None and indent > item_indent:
            # gh-717: a more-indented, marker-less line is the previous
            # item's own wrapped text (commonmark calls this a lazy
            # continuation). Emitting it as a standalone paragraph tore one
            # bullet into a bullet plus an orphan, which is what a 79-col
            # header guarantees for any item longer than a line.
            block[-1] = f"{block[-1]} {stripped}"
        else:
            if block:  # list then prose: the list ends here
                paras.append("\n".join(block))
                block.clear()
                item_indent = None
            prose.append(stripped)
    _flush()
    return paras


# A C declaration line we can attribute a preceding comment to. Captures the
# function name from ``[qualifiers] ret  name(...)``. Deliberately loose: we
# only need the identifier immediately before the '('. The terminator is `;`
# (a prototype) OR `{` (an inline definition in the header, e.g. a
# JM_FORCEINLINE step()/execute body) — gh-385.
_DECL_NAME_RE = re.compile(
    r"^[^/{}#]*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{]*\)\s*[;{]",
    re.MULTILINE | re.DOTALL,
)

# A SINGLE comment block (the inner pattern forbids ``*/`` so it can't bridge
# across an intervening block — e.g. the file-level ``@file`` header) that is
# immediately followed by a function declaration *or* an inline definition
# (whitespace only between; the decl must not itself start a comment). The
# trailing `[;{]` matches a prototype's `;` or an inline body's opening `{`
# (gh-385); the `(…)` requirement before it keeps `typedef struct { … }` and
# other brace blocks without a parameter list from matching.
#
# The decl body excludes `/` so the run cannot cross into a *following*
# comment, and its first character excludes whitespace so it starts on a real
# token. Without both, two adjacent blocks bound the FIRST one to the
# declaration: the decl could begin with the newline after `*/` and then
# swallow the whole second comment, which contains no `;{}` to stop it. That
# made a hand-written block placed above jm's scaffold skeleton silently lose
# to the skeleton (gh-666) — the exact authored-prose-dropped failure the
# derivation contract exists to prevent. Doxygen binds the NEAREST preceding
# block; so does this now. `*` must stay allowed — every pointer parameter
# has one.
#
# gh-654: the opener accepts `/*!` as well as `/**`. Doxygen treats the two as
# the same construct, and a header written with `/*!` derived NOTHING — no
# error, no warning, just a member documented in C and undocumented in Python.
#
# `(?!<)` is a fix, not a widening: `/**<` is a *trailing member doc* (gh-671),
# and the opener matched it. A member doc separated from a following
# declaration by whitespace alone was therefore extracted as that function's
# block, so `int taps; /**< Number of filter taps. */` above a declaration gave
# it the brief `< Number of filter taps.` — the stray `<` reaching both faces.
_DECL_TAIL = r"(?P<decl>[^;{}/*\s][^;{}/]*?\([^;{}/]*\)\s*[;{])"
_BLOCK_THEN_DECL_RE = re.compile(
    r"(?P<raw>/\*[*!](?!<)(?:(?!\*/)[\s\S])*?\*/)\s*" + _DECL_TAIL,
    re.DOTALL,
)

# The line-comment spelling of the same thing: a run of `///` or `//!` lines
# immediately above a declaration. `(?!<)` again excludes the trailing
# member-doc forms, which belong to whatever is declared on their own line and
# must not be read as a block for the NEXT one.
#
# A run is consecutive lines with no blank between them, matching how Doxygen
# reads them; the run then binds to the declaration the same way a block does.
_LINE_RUN_THEN_DECL_RE = re.compile(
    r"(?P<raw>(?:^[ \t]*//[/!](?!<)[^\n]*\n)+)\s*" + _DECL_TAIL,
    re.MULTILINE | re.DOTALL,
)

# A run of nothing but slashes is a section ruler, not documentation. The
# block forms cannot produce this shape, so it arrives only with the line
# forms -- and `////////` sitting above a declaration is a common enough C
# house style that taking it literally would give that function a brief made
# of punctuation.
_RULER_ONLY_RE = re.compile(r"^[/\s]*$")


def _decl_name(decl: str) -> str | None:
    """Extract the function identifier from a C declaration fragment."""
    terminated = decl if decl[-1:] in ";{" else decl + ";"
    m = _DECL_NAME_RE.search(terminated)
    return m.group(1) if m else None


def extract_doc_blocks(header_text: str) -> dict[str, str]:
    """Map each documented C function name to its raw ``/** ... */`` block.

    Only declarations *immediately* preceded by a doc block are included
    (separated by whitespace only). The raw block text retains the
    ``/** ... */`` delimiters so callers can hand it straight to
    :func:`parse_doxygen_block`.

    Parameters
    ----------
    header_text : str
        Full contents of a ``<obj>_core.h``.

    Returns
    -------
    dict
        ``{c_function_name: raw_block_including_delimiters}``.
    """
    out: dict[str, str] = {}
    for pattern in (_BLOCK_THEN_DECL_RE, _LINE_RUN_THEN_DECL_RE):
        for m in pattern.finditer(header_text):
            if _RULER_ONLY_RE.match(m.group("raw")):
                continue
            name = _decl_name(m.group("decl"))
            # A declaration can be preceded by only one comment, so the two
            # patterns cannot disagree about the same name -- but keep the
            # block form authoritative if a header ever manages both.
            if name and name not in out:
                out[name] = m.group("raw")
    return out


# A trailing member doc: the declaration, then `///<` or `/**< ... */` on the
# SAME line. Doxygen calls these "after the member" comments and they are where
# a C author naturally documents a struct field or an enum value. Both opener
# spellings are accepted, as are the `//!<` / `/*!<` variants.
_MEMBER_DOC_RE = re.compile(
    r"^[^/\n]*?\b(?P<name>[A-Za-z_]\w*)\s*"  # the declared name...
    r"(?:\[[^\]]*\])?\s*"  # ...an optional array bound
    r"(?:=[^,;/]*)?\s*"  # ...an optional initialiser (enum values)
    r"[,;]?[ \t]*"  # ...its terminator
    r"(?://[/!]<[ \t]*(?P<line>[^\n]*)"  # `///<` to end of line
    r"|/\*[*!]<[ \t]*(?P<block>.*?)\*/)",  # or `/**< ... */`
    re.MULTILINE | re.DOTALL,
)


def extract_member_docs(header_text: str) -> dict[str, str]:
    """Map each ``///<`` / ``/**<`` trailing member doc to its declared name.

    gh-671. These are "after the member" comments — the form a C author reaches
    for when documenting a struct field or an enum value:

        double phase;      /**< Current phase, radians. */
        int    taps;       ///< Number of filter taps.

    jm turns struct fields into Python properties, so this text is the most
    plausible place a property's documentation already exists. It was invisible
    to derivation, which meant the same sentence had to be re-stated in a
    manifest ``doc=`` or on a getter ``@brief`` and then maintained twice.
    doppler measured 700 of these, ~518 on struct fields, against 369
    properties documented the redundant way.

    Deliberately shallow: the key is the identifier immediately preceding the
    comment on that line, so it works for a field, an enum value, or anything
    else declared one-per-line, without needing to know which construct it sits
    in. Ambiguity is resolved by the caller, which knows whether it is asking
    about a field or an enumerator.

    Returns
    -------
    dict
        ``{name: description}``. A name documented more than once keeps the
        first occurrence, matching the "first declaration wins" reading a
        human would apply.

    Examples
    --------
    >>> extract_member_docs("    double phase;  /**< Phase in radians. */")
    {'phase': 'Phase in radians.'}
    >>> extract_member_docs("    int taps;  ///< Tap count.")
    {'taps': 'Tap count.'}
    >>> extract_member_docs("    FIR_LOW = 0,  ///< Lowpass.")
    {'FIR_LOW': 'Lowpass.'}
    >>> extract_member_docs("    double gain;  /* not a member doc */")
    {}
    """
    out: dict[str, str] = {}
    for m in _MEMBER_DOC_RE.finditer(header_text):
        raw = m.group("line")
        if raw is None:
            raw = m.group("block")
        # A block form may wrap; join it the way the block parser joins prose.
        text = " ".join(
            ln.strip().lstrip("*").strip() for ln in (raw or "").splitlines()
        )
        text = _strip_doxy_inline(text).strip()
        if text:
            out.setdefault(m.group("name"), text)
    return out


# Member docs ride in the same `{name: DoxyBlock}` map the doc-block loaders
# already thread through every generator, under a key a C identifier cannot
# produce. Seven call sites build that map and it reaches the property path
# through several layers; a parallel dict would have to be threaded through all
# of them, and the two are always loaded from the same header at the same time.
_MEMBER_KEY_PREFIX = "<member>"


def member_doc_key(name: str) -> str:
    """The reserved key a struct field's or enum value's doc rides under."""
    return f"{_MEMBER_KEY_PREFIX}{name}"


def member_doc(doc_blocks: "dict | None", name: str) -> str:
    """The trailing ``///<`` / ``/**<`` doc for *name*, or ``""`` (gh-671)."""
    blk = (doc_blocks or {}).get(member_doc_key(name))
    return blk.brief if blk is not None else ""


# gh-761: the arity of each `*_max_out` prototype, riding the same map for the
# same reason the member docs above do — it is loaded from the same header at
# the same moment, and the alternative is threading a parallel dict through
# the seven `make_methods_ctx` call sites and the module stub path.
_MAX_OUT_KEY = "<max_out_state_only>"

# `size_t <comp>_<verb>_max_out ( <params> );` — the declaration jm emits and
# the user then implements. Deliberately tolerant of the formatting a project's
# own clang-format may have applied to it.
_MAX_OUT_ARITY_RE = re.compile(
    r"\bsize_t\s+(\w+_max_out)\s*\(([^)]*)\)\s*;", re.MULTILINE
)


def scan_max_out_arity(header_text: str) -> "frozenset[str]":
    """C function names whose ``_max_out`` takes **only** the state pointer.

    gh-761. jm assumed every ``*_max_out`` takes a trailing count (gh-607),
    but for most kernels the bound is a property of the state, not of the
    block about to be passed: the generated binding requires
    ``capacity >= max(max_out(state), L)``, so when a method cannot emit more
    than it is given, ``0`` is the exact and complete bound and there is
    nothing about the block for the function to know. doppler has 65 of these
    and they split 26 state-only / 39 length-bearing along exactly that line.

    The header is the source of truth because it is where the contract is
    actually written — the manifest records the *method's* shape, which says
    nothing about what its ``_max_out`` sibling needs.

    Returns the full C names (``ddcr_execute_max_out``), so a caller looks up
    ``f"{component}_{name}_max_out"`` without re-deriving the spelling.
    """
    out: set[str] = set()
    for m in _MAX_OUT_ARITY_RE.finditer(header_text):
        params = m.group(2)
        # One parameter and no comma → state only. A `void` parameter list
        # cannot occur here (every `_max_out` takes at least the state).
        if "," not in params:
            out.add(m.group(1))
    return frozenset(out)


def declared_max_outs(header_text: str) -> "frozenset[str]":
    """Every ``*_max_out`` the header declares, whatever its arity (gh-761).

    `_apply._refresh_core_h_decls` protects these from being re-declared: the
    author owns the signature, and jm now *reads* it to decide both the
    binding and the stub. Overwriting it would revert the contract and make
    the derivation unstable — the next apply would read jm's own rewrite back
    and flip both faces to match it.
    """
    return frozenset(
        m.group(1) for m in _MAX_OUT_ARITY_RE.finditer(header_text)
    )


def max_out_prototypes(header_text: str) -> "dict[str, str]":
    """``{name: full declaration text}`` for every ``*_max_out`` (gh-903).

    `declared_max_outs` answers "which of these does the author own", which is
    all `_apply` needs — it edits a header that is still on disk. `jm
    regenerate` **deletes** the header and rebuilds it, so by the time apply
    runs there is nothing left to read the contract off, and the author's
    prototype is silently replaced by jm's default. Preserving it needs the
    declaration itself, not just its name.

    Returns the matched text verbatim, including the trailing semicolon, so
    restoring is a substitution rather than a re-render.
    """
    return {
        m.group(1): m.group(0) for m in _MAX_OUT_ARITY_RE.finditer(header_text)
    }


def restore_max_out_prototypes(
    header_text: str, saved: "dict[str, str]"
) -> "tuple[str, list[str]]":
    """Put author-owned ``*_max_out`` declarations back (gh-903).

    The counterpart of :func:`max_out_prototypes`, for the regenerate cycle:
    capture before the header is deleted, restore after it is rebuilt.

    Only a declaration that actually differs is rewritten, and its name is
    reported — the caller needs to know, because the glue beside it was
    already generated against jm's default and has to be re-derived. Silently
    fixing the header alone would leave a binding that calls the restored
    prototype at the wrong arity, which is a compile error rather than a
    disagreement.

    Returns
    -------
    tuple
        ``(text, changed_names)``. ``changed_names`` is empty for the common
        case where jm's re-render already matches, so the caller can skip the
        second pass entirely.
    """
    changed: list[str] = []
    out = header_text
    for name, decl in saved.items():
        for m in _MAX_OUT_ARITY_RE.finditer(header_text):
            if m.group(1) != name or m.group(0) == decl:
                continue
            out = out.replace(m.group(0), decl, 1)
            changed.append(name)
    return out, changed


def max_out_arity_key() -> str:
    """The reserved key `scan_max_out_arity`'s result rides under."""
    return _MAX_OUT_KEY


def max_out_is_state_only(doc_blocks: "dict | None", c_name: str) -> bool:
    """True when *c_name*'s declaration takes only the state (gh-761).

    False when the header declares a count parameter **and** when there is no
    declaration at all — a method jm is scaffolding for the first time has no
    prototype to read, and keeps gh-607's count-bearing default.
    """
    return c_name in ((doc_blocks or {}).get(_MAX_OUT_KEY) or frozenset())


def _strip_comment(raw: str) -> list[str]:
    """Strip a doc comment's delimiters and per-line decoration.

    Handles every spelling :func:`extract_doc_blocks` can hand back: the
    ``/** … */`` and ``/*! … */`` block forms, and a run of ``///`` or ``//!``
    lines. Returns the interior lines with leading decoration removed but
    otherwise preserving relative content (blank lines kept for paragraph
    splitting).

    gh-654: the ``/*!`` opener used to fall through to the two-character
    ``/*`` branch, leaving a literal ``!`` at the front of the brief.

    Examples
    --------
    >>> _strip_comment("/** @brief One. */")
    ['@brief One.']
    >>> _strip_comment("/*! @brief One. */")
    ['@brief One.']
    >>> _strip_comment("/// @brief One.\\n/// More.")
    ['@brief One.', 'More.']
    >>> _strip_comment("//! @brief One.\\n//!\\n//! Second paragraph.")
    ['@brief One.', '', 'Second paragraph.']
    >>> _strip_comment("////////\\n/// @brief One.\\n////////")
    ['@brief One.']
    """
    body = raw.strip()
    if body.startswith("///") or body.startswith("//!"):
        # A line-comment run: the marker is the whole decoration, and a bare
        # `///` is a blank line, which is what separates paragraphs. A line of
        # nothing but slashes is a ruler and reads as the same blank -- taking
        # it literally would put punctuation in the middle of a brief.
        lines: list[str] = []
        for ln in body.splitlines():
            s = ln.strip()[3:]
            if _RULER_ONLY_RE.match(s):
                s = ""
            elif s.startswith(" "):
                s = s[1:]
            lines.append(s.rstrip())
        return _trim_blank_ends(lines)
    if body.startswith("/**") or body.startswith("/*!"):
        body = body[3:]
    elif body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("*"):
            s = s[1:]
        # drop exactly one leading space left by the "* " decoration
        if s.startswith(" "):
            s = s[1:]
        lines.append(s.rstrip())
    return _trim_blank_ends(lines)


def _trim_blank_ends(lines: list[str]) -> list[str]:
    """Drop leading and trailing blank lines, keeping interior ones."""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


# Any Doxygen command at the head of a line: either prefix (``@brief`` and
# ``\brief`` are the same command — gh-650), an identifier, an optional
# ``[direction]`` group for ``@param[out]``, then whitespace or end-of-line.
#
# That trailing requirement is load-bearing. Without it ``@f$ 20\log(g) @f$``
# reads as a command named ``f`` with the argument ``$ 20\log(g)``, and inline
# math at the head of a line would be quarantined out of the prose.
_CMD_RE = re.compile(r"^[@\\]([A-Za-z]+)(?:\[([^\]]*)\])?(?=\s|$)\s*(.*)$")

# Commands with a structured destination on DoxyBlock. Everything else that
# _CMD_RE recognises is quarantined into `tags`.
_BLOCK_CMDS = frozenset({"brief", "param", "return", "returns"})

# Inline markup handled by _strip_doxy_inline, never a block command. A line
# that happens to *start* with one ("@ref demo_reset is the counterpart")
# is prose, and must not be swallowed as a tag.
_INLINE_CMDS = frozenset({"p", "c", "a", "e", "b", "ref"})

# @code / @endcode, in either prefix. Kept separate from _CMD_RE because
# `@code{.py}` puts a brace where _CMD_RE requires whitespace.
_CODE_OPEN_RE = re.compile(r"^[@\\]code\b")
_CODE_CLOSE_RE = re.compile(r"^[@\\]endcode\b")

_PARAM_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$")


def _norm_brief(text: str) -> str:
    """Fold a brief to the form the scaffold templates are compared in."""
    return text.strip().rstrip(".").replace("_", " ").strip().lower()


def _fold(text: str) -> str:
    """Reduce a brief to letters and digits, for template comparison.

    jm's own scaffolds do not agree with themselves about how to spell the
    object: ``create``/``destroy`` interpolate the component id
    (``Create a my_filter instance.``) while ``reset`` interpolates the class
    name (``Reset MyFilter to its post-create state.``). Comparing on
    :func:`_norm_brief` alone therefore matched one and missed the other --
    ``my_filter`` folds to ``my filter``, ``MyFilter`` folds to ``myfilter``.

    Dropping the separators makes the comparison indifferent to that, which is
    the right call: both spellings are jm writing about the same object, and a
    human writing either one verbatim is writing jm's boilerplate.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


# jm's own scaffold @brief for the built-in step()/steps() methods, by I/O
# shape. Normalized by _norm_brief.
_STEP_SCAFFOLD_BRIEFS = frozenset(
    {
        "advance state by one tick (no i/o)",
        "consume one input sample (sink; no output)",
        "generate a block of output samples",
        "generate one output sample from internal state",
        "process a block of input samples (no output)",
        "process a block of samples",
        "process n iterations (no scalar output)",
        "process one input buffer and return a result",
        "process one input buffer (no scalar output)",
        "process one input sample",
    }
)


def scaffold_briefs(member: str, owner: str = "") -> set[str]:
    """Normalized briefs jm itself scaffolds for *member* of *owner*.

    These are jm's **specific** template strings — ``Get current gain.``,
    ``Reset fir to its post-create state.`` — not the generic ``<member>.``
    sentinel, which :func:`is_scaffold_doc` handles separately because it is
    much weaker evidence (see there).

    *member* is the bare verb (``execute``, ``get_gain``, ``create``), not the
    ``<owner>_``-prefixed C name. *owner* is the component or module; the
    lifecycle templates interpolate it, so omitting it simply drops those
    entries rather than matching the wrong thing.

    Parameters
    ----------
    member : str
        Bare member name, as it appears after the ``<owner>_`` prefix.
    owner : str, optional
        Component/module name. Required for the lifecycle templates.

    Returns
    -------
    set of str
        Every brief jm's own scaffolds could have written here, normalized by
        :func:`_norm_brief` so a caller compares like with like.

    Examples
    --------
    >>> sorted(scaffold_briefs("set_gain"))
    ['set gain', 'set gain from src']
    >>> "create a fir instance" in scaffold_briefs("create", "fir")
    True
    >>> scaffold_briefs("tune")
    set()
    """
    out: set[str] = set()
    if owner:
        out |= {
            _norm_brief(f"Create a {owner} instance"),
            _norm_brief(f"Destroy a {owner} instance and release all memory"),
            _norm_brief(f"Reset {owner} to its post-create state"),
        }
    if member.startswith("get_"):
        field = member[4:]
        out |= {
            _norm_brief(f"Get current {field}"),
            _norm_brief(f"Get a read-only pointer to {field}"),
            _norm_brief(f"Return a read-only pointer to {field}"),
        }
    if member.startswith("set_"):
        field = member[4:]
        out |= {
            _norm_brief(f"Set {field}"),
            _norm_brief(f"Set {field} from src"),
        }
    if member in ("step", "steps"):
        out |= _STEP_SCAFFOLD_BRIEFS
    return out


def is_scaffold_doc(
    block: DoxyBlock, member: str = "", owner: str = ""
) -> bool:
    """True when *block* is jm's own scaffold boilerplate, not authored doc.

    **The** scaffold-sentinel test (gh-666). Deriving Python docs from jm's
    own template output would (a) be no richer than the name-based fallback
    and (b) break idempotence — a manifest-only rebuild has no header to read,
    so it must produce what a fresh scaffold produces.

    The signal is the **brief**, in two strengths.

    A brief matching one of jm's specific templates (:func:`scaffold_briefs` —
    ``Get current gain.``) is conclusive on its own. Those scaffolds emit
    boilerplate ``@param``/``@return`` alongside the brief (``@param state
    Must be non-NULL.``), so matching the brief means the whole block is
    boilerplate; trying to also match the generated ``@param`` prose would be a
    second, fragile copy of the templates that turns every wording tweak into a
    silent behaviour change.

    A brief that is merely the member's own name (``@brief tune.``) is much
    weaker — it is equally what a terse author writes — so it counts only when
    nothing else in the block was filled in. That way a half-filled skeleton
    (``@brief tune.`` left alone, ``@param hz  Tuning frequency in Hz.``
    written) keeps the prose the author did write instead of discarding it.

    Body prose and ``@code`` examples are the escape hatch in both strengths:
    no jm scaffold emits either (the skeleton deliberately carries no runnable
    example — a placeholder ``>>> TODO`` would be executed by the generated
    project's doctest gate), so their presence proves an author has been here.

    Parameters
    ----------
    block : DoxyBlock
        The parsed block to classify.
    member : str, optional
        Bare member name. Without it nothing can match.
    owner : str, optional
        Component/module name, for the lifecycle templates.

    Returns
    -------
    bool
        True when the block carries nothing an author wrote.

    Examples
    --------
    >>> bare = parse_doxygen_block("@brief tune.")
    >>> is_scaffold_doc(bare, "tune")
    True
    >>> half = parse_doxygen_block("@brief tune.\\n@param hz  In Hz.")
    >>> is_scaffold_doc(half, "tune")
    False
    >>> tmpl = parse_doxygen_block("@brief Get current g.\\n@param state  X.")
    >>> is_scaffold_doc(tmpl, "get_g")
    True
    """
    if block.body or block.examples:
        return False
    brief = _norm_brief(block.brief)
    if not brief or not member:
        return False
    if _fold(brief) in {_fold(s) for s in scaffold_briefs(member, owner)}:
        return True
    if _fold(brief) != _fold(member):
        return False
    # Generic ``@brief <member>.`` — only an untouched skeleton counts.
    return not block.returns.strip() and not any(
        desc.strip() for _n, desc in block.params
    )


# One parameter of a C prototype, reduced to its declared name: the last
# identifier before an optional `[]` / bit-width. `void` alone and a lone
# ellipsis have no name and yield nothing.
_PARAM_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*$")


def _decl_signature(decl: str) -> tuple[str, list[str], bool] | None:
    """Split a generated C prototype into ``(fn, param_names, returns_value)``.

    Reads the declaration jm is about to write rather than re-deriving the
    signature from the manifest, so the skeleton cannot describe a parameter
    the prototype does not have — a mismatched ``@param`` is precisely what
    Doxygen's ``WARN_NO_PARAMDOC`` companion warning reports.

    Returns ``None`` for anything that is not a plain prototype (a function
    pointer parameter, a macro, a multi-declarator line), so an unusual shape
    gets no skeleton rather than a wrong one.
    """
    body = decl.strip().rstrip(";").strip()
    open_paren = body.find("(")
    if open_paren < 0 or not body.endswith(")"):
        return None
    head, args = body[:open_paren], body[open_paren + 1 : -1]
    # A nested paren means a function-pointer or attribute shape; skip it.
    if "(" in args or ")" in args:
        return None
    m = _PARAM_NAME_RE.search(head.replace("*", " "))
    if m is None:
        return None
    fn = m.group(1)
    ret = head[: head.rfind(fn)].strip()
    returns_value = ret.replace("*", " ").split() != ["void"]

    names: list[str] = []
    for raw_arg in args.split(","):
        arg = raw_arg.strip()
        if not arg or arg == "void" or arg == "...":
            continue
        pm = _PARAM_NAME_RE.search(arg)
        if pm is None:
            return None
        names.append(pm.group(1))
    return fn, names, returns_value


def scaffold_doc_block(decl: str, member: str, indent: str = "") -> str:
    """Return jm's prose-free Doxygen skeleton for the prototype *decl*.

    The counterpart of :func:`is_scaffold_doc`, kept beside it deliberately:
    what jm emits and what jm refuses to derive have to stay the same thing,
    and they drifted apart once already (gh-666).

    The skeleton supplies **structure only** — the ``@brief`` sentinel and one
    ``@param`` per parameter, in signature order, with a ``@return`` when the
    function returns a value. It deliberately writes **no descriptions**:

    * an invented description (``@param hz  double parameter.``) is not
      documentation, and once it is in a header jm can no longer tell it from
      prose a human wrote, so the block starts deriving into the ``.pyi`` as
      if authored;
    * a bare ``@param hz`` still satisfies Doxygen's ``WARN_NO_PARAMDOC``
      (verified against Doxygen 1.15), so a fresh scaffold is not noisy under
      the flag a project may have on, while *omitting* a parameter still
      warns — the signal that flag exists for is untouched;
    * no ``@code`` block, because a placeholder example would be executed by
      the generated project's doctest gate.

    Parameters
    ----------
    decl : str
        The C prototype the comment will sit above.
    member : str
        Bare member name, used for the ``@brief`` sentinel.
    indent : str, optional
        Prefix for every emitted line.

    Returns
    -------
    str
        The comment block with no trailing newline, or ``""`` when *decl* is
        not a shape the skeleton can describe faithfully.

    Examples
    --------
    >>> print(scaffold_doc_block("double fir_tune(fir_state_t *s, double hz);",
    ...                          "tune"))
    /**
     * @brief tune.
     *
     * @param s
     * @param hz
     * @return
     */
    >>> scaffold_doc_block("void (*cb)(int);", "cb")
    ''
    """
    sig = _decl_signature(decl)
    if sig is None:
        return ""
    _fn, names, returns_value = sig
    lines = [f"{indent}/**", f"{indent} * @brief {member}."]
    if names or returns_value:
        lines.append(f"{indent} *")
    lines += [f"{indent} * @param {n}" for n in names]
    if returns_value:
        lines.append(f"{indent} * @return")
    lines.append(f"{indent} */")
    return "\n".join(lines)


def authored_class_brief(
    doc_blocks: "dict | None",
    create_fn: str,
    manifest_doc: str = "",
) -> str:
    """The authored summary for a wrapped type, or ``""`` if there is none.

    One definition of the precedence every regeneration path uses for the
    runtime class docstring (``tp_doc``): manifest ``doc=`` outranks the
    header's ``@brief`` on the constructor, and jm's own scaffold boilerplate
    counts as neither (``_object._load_doc_blocks`` has already filtered it).

    Returning ``""`` rather than a fallback is deliberate. The fallback differs
    by caller -- a standalone object's template seeds "``<C>`` component. Wraps
    ``<c>_state_t``." while the module aggregator uses "``<C>`` type." -- and a
    caller that overwrote its own seeded default unconditionally would make a
    freshly scaffolded project report STALE against itself, because
    ``jm object`` renders the binding without doc blocks and ``jm apply``
    renders it with them.

    Parameters
    ----------
    doc_blocks : dict or None
        ``{c_function_name: DoxyBlock}`` from ``_object._load_doc_blocks``.
    create_fn : str
        The constructor's C name -- ``<obj>_create``, or the ``create_fn``
        override, which is what ``tp_init`` actually calls (gh-602).
    manifest_doc : str, optional
        The object's manifest ``doc=``, which outranks the header.

    Returns
    -------
    str
        The authored summary, or ``""`` when nothing was authored.

    Examples
    --------
    >>> blk = parse_doxygen_block("@brief Log-domain AGC.")
    >>> authored_class_brief({"agc_create": blk}, "agc_create")
    'Log-domain AGC.'
    >>> authored_class_brief({"agc_create": blk}, "agc_create", "From TOML.")
    'From TOML.'
    >>> authored_class_brief({}, "agc_create")
    ''
    """
    if manifest_doc:
        return manifest_doc
    blk = (doc_blocks or {}).get(create_fn)
    return blk.brief if (blk and blk.brief) else ""


def parse_doxygen_block(raw: str, name: str | None = None) -> DoxyBlock | None:
    """Parse one raw ``/** ... */`` block into a :class:`DoxyBlock`.

    Tolerant of missing or partial tags. Continuation lines (no ``@tag``
    prefix) extend the most recent ``@brief``/``@param``/``@return``.

    Parameters
    ----------
    raw : str
        The comment text, with or without ``/** */`` delimiters.
    name : str, optional
        If given, a block whose only content is the trivial template brief
        ``<name>.`` (jm's own scaffold output, ignoring ``_``/space and case)
        is treated as empty and ``None`` is returned — so a freshly
        scaffolded header keeps the name-based fallback until real docs exist.

    Returns
    -------
    DoxyBlock or None
        ``None`` when the block has no usable content.
    """
    lines = _strip_comment(raw)
    if not lines:
        return None

    brief_parts: list[str] = []
    body_lines: list[str] = []
    params: list[tuple[str, str]] = []
    param_dirs: dict[str, str] = {}
    return_parts: list[str] = []
    example_lines: list[str] = []
    tags: list[list[str]] = []
    # current target: "brief" | "body" | "param" | "return" | "tag"
    target = "brief"
    saw_brief_tag = False
    in_code = False

    for ln in lines:
        stripped = ln.strip()
        # @code ... @endcode delimits a verbatim doctest example. Capture the
        # interior lines (the `* ` decoration is already stripped) so they
        # render into a numpy ``Examples`` block / runnable doctest.
        if in_code:
            if _CODE_CLOSE_RE.match(stripped):
                in_code = False
                target = "body"
            else:
                example_lines.append(ln)
            continue
        if _CODE_OPEN_RE.match(stripped):
            in_code = True
            continue

        cmd_m = _CMD_RE.match(stripped)
        cmd = cmd_m.group(1) if cmd_m else ""
        if cmd_m and cmd not in _INLINE_CMDS:
            direction, rest = cmd_m.group(2), cmd_m.group(3).strip()
            if cmd == "brief":
                target, saw_brief_tag = "brief", True
                if rest:
                    brief_parts.append(rest)
            elif cmd == "param":
                pm = _PARAM_RE.match(rest)
                if pm:
                    params.append([pm.group(1), pm.group(2).strip()])
                    if direction:
                        param_dirs[pm.group(1)] = direction.strip()
                    target = "param"
                else:
                    target = "body"  # malformed @param: ignore tag, keep text
            elif cmd in _BLOCK_CMDS:  # return / returns
                target = "return"
                if rest:
                    return_parts.append(rest)
            else:
                # gh-641: a command with no structured destination is held
                # here, NOT appended to whatever field happened to be open.
                # The old behaviour made the damage depend on position — the
                # same @note landed in the summary, the body, or the Returns
                # description depending only on which tag preceded it.
                tags.append([cmd, rest])
                target = "tag"
            continue

        if not stripped:
            # blank line ends the brief (the summary is a single paragraph,
            # whether tagged @brief or untagged lead text), ends a quarantined
            # tag's paragraph, and separates body paragraphs.
            if target == "brief" and brief_parts:
                target = "body"
            if target == "tag":
                target = "body"
            if target == "body" and body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue

        if target == "brief":
            brief_parts.append(stripped)
        elif target == "param":
            params[-1][1] = (params[-1][1] + " " + stripped).strip()
        elif target == "return":
            return_parts.append(stripped)
        elif target == "tag":
            tags[-1][1] = (tags[-1][1] + " " + stripped).strip()
        else:
            # gh-717: body lines keep their own indentation. A wrapped
            # list-item continuation is distinguishable from a new paragraph
            # *only* by being more indented than its item, and
            # `group_paragraphs` cannot recover that once it is stripped.
            # Every other target still stores the stripped form — they are
            # single logical values, not layout.
            body_lines.append(ln.rstrip())

    # If there was no @brief tag, the first body paragraph is the brief.
    if not saw_brief_tag and not brief_parts and body_lines:
        first: list[str] = []
        while body_lines and body_lines[0].strip():
            # The brief is one sentence, not layout — strip the indentation
            # gh-717 now preserves, or a wrapped line embeds it mid-sentence.
            first.append(body_lines.pop(0).strip())
        brief_parts = first
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)

    brief = " ".join(brief_parts).strip()
    # collapse body into paragraphs (strip trailing blank)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    returns = " ".join(return_parts).strip()
    # trim trailing blank lines from the captured example
    while example_lines and not example_lines[-1].strip():
        example_lines.pop()
    # Reduce Doxygen inline word-references (@p/@c/@ref name) to the bare word
    # so the synthesized Python docstrings read cleanly. Examples are left as-is
    # (they are verbatim @code blocks).
    block = DoxyBlock(
        # gh-652: `@f$ ... @f$` -> `:math:` alongside the inline-marker strip,
        # so every consumer of a parsed block gets it — the tag sections are
        # not the only place an author writes math (a `@param` description is
        # the commonest).
        brief=_clean(brief),
        body=[_clean(b) for b in body_lines],
        params=[(n, _clean(d)) for n, d in params],
        returns=_clean(returns),
        examples=example_lines,
        param_dirs=param_dirs,
        tags=[(c, _clean(t)) for c, t in tags],
    )

    # Quarantined tags deliberately do NOT count as content here: nothing
    # renders them yet, so a block carrying only an @note still falls back to
    # the name-based stub, which is the behaviour the contract documents. When
    # gh-652 gives them numpy sections this test gains `or block.tags`.
    if not (brief or block.body or block.params or returns or example_lines):
        return None
    # jm's own scaffold output (e.g. "@brief myverb." over generated @params)
    # reads as empty, so the name-based fallback stands until an author writes
    # something. `name` is the bare member; the owner-dependent lifecycle
    # templates are matched by the caller that knows the owner (_object).
    if name and is_scaffold_doc(block, name):
        return None
    return block


# gh-744: every generated docstring line has to fit the same 79-column rule as
# every other file in a jm project, so the widths below are *derived* from that
# target rather than guessed. They used to be three unrelated literals (72, 69,
# 68) chosen without the caller's indent in the budget, which is why "wrapped"
# prose still landed on column 80.
STUB_TARGET_WIDTH = 79

# The deepest indent a docstring is emitted at: 8 for a class member, 4 for a
# module-level function. Both faces of a member share `_numpy_sections`, and
# the runtime face (`render_runtime_doc`) carries no indent at all, so a width
# derived from the caller's `indent` would have to be threaded onto the runtime
# API as well -- and any call site that got it wrong would make the two faces
# wrap differently, which is exactly the drift `test_gh642_runtime_doc_parity`
# exists to forbid. Budgeting for the deepest case keeps one width for both
# faces. The cost is that a module-level function wraps four columns narrower
# than it strictly must, which reads as a consistent prose width across the
# file rather than as a defect.
_MEMBER_INDENT = 8

# Body prose sits at the section indent; a numpy description is indented four
# further; the summary shares its line with the opening `"""`; a
# `.. deprecated::` note is indented three, as reST requires.
DOC_WIDTH = STUB_TARGET_WIDTH - _MEMBER_INDENT
DESC_WIDTH = DOC_WIDTH - 4
SUMMARY_HEAD_WIDTH = DOC_WIDTH - 3
_DEPRECATED_WIDTH = DOC_WIDTH - 3


# A *class* docstring sits one level shallower than a member's: 4 spaces, with
# its numpy descriptions at 8. Two producers emit one -- `_stubs` for a
# component and `_composer` for the composer's OO types -- so the budgets live
# here with the rest rather than being re-derived in each.
CLASS_INDENT = 4
CLASS_DOC_WIDTH = STUB_TARGET_WIDTH - CLASS_INDENT
CLASS_DESC_WIDTH = CLASS_DOC_WIDTH - 4


def _wrap(text: str, width: int = DOC_WIDTH) -> list[str]:
    """Soft-wrap *text* to *width* columns; never splits a long token."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def wrap_summary(text: str, width: int = DOC_WIDTH) -> list[str]:
    """Soft-wrap a docstring summary, paying for the opening ``\"\"\"``.

    gh-744. The summary was the one section spliced in unwrapped, so a long
    ``@brief`` became a single enormous line -- 1396 columns at the worst
    measured case. It cannot simply go through :func:`_wrap`, because its
    first line is three columns shorter than the rest: the caller
    concatenates it onto the opening delimiter, and only that line pays for
    it.

    Parameters
    ----------
    text : str
        The summary sentence, already resolved from ``@brief``/``doc=``.
    width : int, optional
        Budget for the continuation lines. The first line gets ``width - 3``.

    Returns
    -------
    list of str
        One entry per line, unindented. Never empty for non-empty *text*, so
        the caller can keep splicing ``result[0]`` onto its delimiter.

    Examples
    --------
    >>> wrap_summary("Short.")
    ['Short.']
    >>> lines = wrap_summary("word " * 30)
    >>> len(lines[0]) <= DOC_WIDTH - 3
    True
    >>> max(len(x) for x in lines[1:]) <= DOC_WIDTH
    True
    """
    head = _wrap(text, width - 3)
    if len(head) <= 1:
        return head
    # Only line 0 is short; re-flow everything after it at the full width so
    # the delimiter does not narrow the whole paragraph.
    return [head[0]] + _wrap(" ".join(head[1:]), width)


def _leave_room_for_the_closer(lines: list[str], budget: int) -> list[str]:
    """Re-wrap so a formatter can pull the closing ``\"\"\"`` up safely.

    gh-746. jm emits a compliant block whose closing delimiter sits alone::

        \"\"\"Bonferroni per-cell false-alarm probability over the cells.
        \"\"\"

    `ruff format` joins those two lines. It enforces ``line-length`` on code,
    not on string *content*, so it does not check what the joined line
    measures — and at 79 columns exactly, the join lands on 82. The result is
    that pointing a formatter at the stubs (which is what gh-746 asks for)
    silently undoes gh-744 in a six-column window.

    jm cannot stop ruff joining, so it stops producing the shape ruff wants
    to join badly: the final content line is kept three columns short, so
    pulling the delimiter up stays within the target whether or not anything
    ever does it. The output is compliant either way; this only makes it a
    *fixed point* of the formatter rather than merely correct today.

    Moving the last word down is enough — and is the smallest edit that
    restores the invariant — because the width lost is one word, not one
    line.

    Examples
    --------
    >>> _leave_room_for_the_closer(["aaa bbb"], 8)
    ['aaa', 'bbb']
    >>> _leave_room_for_the_closer(["aa", "bb"], 8)
    ['aa', 'bb']
    """

    def _emitted(seq: list[str]) -> int:
        """Columns the final line will actually occupy once rendered.

        Line 0 carries the opening delimiter as well, which is exactly the
        case the first version of this missed: a summary short enough to be
        the *only* content line is still three columns longer than it looks.
        """
        return len(seq[-1]) + (3 if len(seq) == 1 else 0)

    while lines and _emitted(lines) > budget - 3:
        words = lines[-1].split()
        if len(words) < 2:
            break  # a single unsplittable token: leave it rather than lie
        lines = lines[:-1] + [" ".join(words[:-1]), words[-1]]
    return lines


def summary_docstring(
    text: str, indent: int = 8, width: int = STUB_TARGET_WIDTH
) -> list[str]:
    """Render a summary-only docstring block, wrapped to the column target.

    gh-744. A property's ``.pyi`` docstring is a bare summary with no numpy
    sections, so it does not go through :func:`render_numpy_doc` -- it had its
    own one-line emitter in ``_context/_methods``, which meant a long
    ``@brief`` came out at whatever length it was written (358 columns in the
    measured case). This is that emitter, with the wrap the rest of the module
    already applies.

    A summary that fits stays on one line, ``\"\"\"like this.\"\"\"``, because
    that is what every existing stub looks like and reflowing the short ones
    would churn every project's diff for nothing. The one-line budget has to
    pay for *both* delimiters, which is why it is six narrower rather than
    three.

    Parameters
    ----------
    text : str
        The summary sentence.
    indent : int, optional
        Leading spaces. 8 for a property inside a class.
    width : int, optional
        Column target; defaults to the project-wide 79.

    Returns
    -------
    list of str
        Complete docstring lines, delimiters included.

    Examples
    --------
    >>> summary_docstring("Sample rate in Hz.", indent=4)
    ['    \"\"\"Sample rate in Hz.\"\"\"']
    >>> block = summary_docstring("Sample rate in hertz. " * 5, indent=4)
    >>> len(block), max(len(ln) for ln in block) <= STUB_TARGET_WIDTH
    (3, True)
    >>> block[-1]
    '    \"\"\"'
    """
    pad = " " * indent
    budget = width - indent
    text = text.strip()
    if len(text) <= budget - 6:  # room for both delimiters on the one line
        return [f'{pad}"""{text}"""']
    lines = _leave_room_for_the_closer(wrap_summary(text, budget), budget)
    return (
        [f'{pad}"""{lines[0]}']
        + [f"{pad}{ln}" for ln in lines[1:]]
        + [f'{pad}"""']
    )


class ClassParam(NamedTuple):
    """One entry in a class docstring's numpy ``Parameters`` block.

    Attributes
    ----------
    type_line : str
        The ``name : annotation, default X`` line, **unindented**. Built by
        the caller, because deriving it is the one genuinely producer-specific
        step: a component reads init-params, a handle reads ``create_args``,
        the composer reads field dicts, and each renders defaults its own way.
    notes : tuple of str
        Free prose beneath the type line — the parameter description, an enum
        choice list, or both. Each is wrapped independently at
        `CLASS_DESC_WIDTH` and indented to the numpy hanging indent, so the
        caller never does its own wrapping.
    """

    type_line: str
    notes: tuple[str, ...] = ()


def class_docstring(
    summary: str,
    *,
    body: "Sequence[str]" = (),
    params: "Sequence[ClassParam]" = (),
    raises: "Sequence[tuple[str, str]]" = (),
    warns: "Sequence[tuple[str, str]]" = (),
    trailer: "Sequence[str]" = (),
    blank_before_close: bool = False,
) -> list[str]:
    """Lay out a numpy-style class docstring, wrapped to the column target.

    gh-747. jm had **three** hand-written builders emitting
    ``["    Parameters", "    ----------"]`` — ``_stubs``, ``_composer``, and
    ``_handle`` — and gh-744 fixed the wrapping in two of them. The third kept
    a hard-coded ``_wrap(para, 72)`` and emitted parameter descriptions at
    whatever length they were written, which is how a 108-column line reached
    doppler's ``wfm_sink.pyi`` after gh-744 was declared done. Patching the
    third copy would only have reset the clock; this is the one layout, and
    the three callers now supply content to it rather than re-deriving it.

    What is shared is the *layout*: the widths, the 4-space class indent, the
    8-space numpy hanging indent, the blank-line rules, and the delimiters.
    What stays with each caller is deriving the content — see `ClassParam`.

    Parameters
    ----------
    summary : str
        The summary sentence, spliced onto the opening ``\"\"\"``.
    body : sequence of str, optional
        Extended-description paragraphs between the summary and the
        ``Parameters`` block. Each is wrapped at `CLASS_DOC_WIDTH` and
        followed by a blank line.
    params : sequence of ClassParam, optional
        The ``Parameters`` block. Omitted entirely when empty — an empty
        numpy section header is worse than none.
    raises : sequence of tuple(str, str), optional
        ``(exception_class, description)`` the constructor may raise, from the
        manifest's ``create_error``/``create_error_message`` (gh-805 §F).
    warns : sequence of tuple(str, str), optional
        ``(warning_category, description)`` construction may emit, from
        ``[[<obj>.warnings]]`` (gh-805 §F). Rendered like *raises* and placed
        after it, which is numpydoc's order.
    trailer : sequence of str, optional
        Ready-indented lines appended before the closing delimiter, for a
        section this function does not model (``_stubs``'s ``Examples``, whose
        doctests must not be wrapped or re-indented).
    blank_before_close : bool, optional
        Emit a blank line after the ``Parameters`` block even when *trailer*
        is empty. ``_stubs`` has always done so and the other two never have;
        the difference is preserved rather than normalised, because changing
        it would churn the stub of every existing project for no gain.

    Returns
    -------
    list of str
        Complete docstring lines, delimiters included, indented ready to
        splice into a class body.

    Examples
    --------
    >>> class_docstring("Short.")
    ['    \"\"\"Short.\"\"\"']
    >>> class_docstring("A thing.", params=[ClassParam("n : int")])
    ['    \"\"\"A thing.', '', '    Parameters', '    ----------', '    n : int', '    \"\"\"']
    >>> block = class_docstring(
    ...     "A thing.",
    ...     params=[ClassParam("n : int", ("word " * 40,))],
    ... )
    >>> max(len(ln) for ln in block) <= STUB_TARGET_WIDTH
    True
    """
    pad = " " * CLASS_INDENT
    # gh-805 §F: the diagnostic sections, in numpydoc's order. Paired with
    # their headings here so the "is there anything to emit" test below and
    # the emission itself read the same list — the shape where a section is
    # added to one and forgotten in the other.
    diagnostics = [
        (heading, list(entries))
        for heading, entries in (("Raises", raises), ("Warns", warns))
        if entries
    ]
    # Nothing but a summary: this is exactly `summary_docstring`'s job,
    # including keeping a short one on a single line so existing stubs do not
    # churn, and leaving room for a formatter to pull the closer up (gh-746).
    if not body and not params and not trailer and not diagnostics:
        return summary_docstring(summary, indent=CLASS_INDENT)

    head = wrap_summary(summary, CLASS_DOC_WIDTH)
    lines = [f'{pad}"""{head[0]}']
    lines += [f"{pad}{ln}" for ln in head[1:]]
    lines += [""]

    for para in body:
        lines += [f"{pad}{w}" for w in _wrap(para, CLASS_DOC_WIDTH)] + [""]

    if params:
        lines += [f"{pad}Parameters", f"{pad}----------"]
        for p in params:
            lines.append(f"{pad}{p.type_line}")
            for note in p.notes:
                lines += [
                    f"{pad}    {w}" for w in _wrap(note, CLASS_DESC_WIDTH)
                ]
        if blank_before_close or trailer or diagnostics:
            lines += [""]

    # `typed_section` opens with its own blank separator, so it is dropped when
    # the block above already left one — two blanks between numpy sections is
    # a paragraph break, and numpydoc reads it as the section body ending.
    for heading, entries in diagnostics:
        sec = typed_section(heading, entries, CLASS_DESC_WIDTH)
        if lines and lines[-1] == "":
            sec = sec[1:]
        lines += [f"{pad}{ln}".rstrip() for ln in sec]
        if blank_before_close or trailer:
            lines += [""]

    lines += list(trailer)
    lines.append(f'{pad}"""')
    return lines


def example_budget(indent: int, width: int = STUB_TARGET_WIDTH) -> int:
    """Content columns an authored ``@code`` line may use at *indent*.

    gh-752. An author writes ``@code`` inside a C comment, where the visible
    margin is the header's own 79 columns minus the ``` * ``` decoration. jm
    strips that decoration and re-indents the line to sit inside a docstring,
    so the budget the author must actually hit is ``79 - indent`` — 71 for a
    class member, 75 for a module-level function.

    Neither number is visible from the header, and which applies depends on
    where the documented function ends up in the generated stub. That is why
    this lives in jm: a downstream gate can see the overflow but cannot
    compute the budget, so it cannot tell the author what to aim for.
    """
    return width - indent


def example_overflows(
    examples: list[str],
    indent: int,
    width: int = STUB_TARGET_WIDTH,
) -> list[tuple[str, int]]:
    """``(line, emitted_columns)`` for each ``@code`` line too wide to fit.

    Reports rather than repairs, deliberately. These lines are the author's:
    they are doctests, so re-wrapping a ``>>>`` changes what runs, and the
    overflow is overwhelmingly a trailing aligned comment whose column is a
    deliberate choice. jm's job is to say which line, by how much, and what
    the target is — the edit belongs in the header.

    Parameters
    ----------
    examples : list of str
        Verbatim ``@code`` interior lines, as parsed.
    indent : int
        Leading spaces the renderer will add. 8 inside a class, 4 for a
        module-level function.
    width : int, optional
        Column target; defaults to the project-wide 79.

    Returns
    -------
    list of tuple(str, int)
        One entry per overflowing line, in order, with the column count it
        will actually occupy.

    Examples
    --------
    >>> example_overflows([">>> x = 1"], 8)
    []
    >>> line = ">>> obj.step(1.0)" + " " * 4 + "# " + "a" * 60
    >>> example_overflows([line], 8)[0][1]
    91
    """
    out: list[tuple[str, int]] = []
    for ex in examples:
        text = ex.rstrip()
        if not text:
            continue
        cols = indent + len(text)
        if cols > width:
            out.append((text, cols))
    return out


def wrap_structured_line(line: str, width: int = DOC_WIDTH) -> list[str]:
    """Wrap one list item, hanging-indenting its continuations.

    gh-653 emits a structured paragraph verbatim, on the principle that its
    line breaks are the author's. gh-717 then folds a wrapped continuation
    back into its item — which is correct, and which makes the item longer
    than the line it has to fit: doppler's ``nearest:`` bullet comes back at
    118 columns, undoing gh-744 for exactly the shape gh-653 exists to
    protect.

    Both rules survive if the item is re-wrapped *within itself*::

        - nearest: the floor or the next index, whichever `point` is
          closer to (an exact 0.5 tie selects the floor index)

    The break moves, the structure does not, and the reader sees a list. The
    hanging indent is the marker's own width, so continuations line up under
    the item text the way every markdown renderer draws them.

    **A table row is returned untouched.** Its columns *are* its meaning, and
    a wrapped ``| a | b |`` is not a narrower table — it is a broken one. A
    table wider than the target stays wide, and the gh-744 gate reports it,
    which is the honest outcome for something jm cannot fix without
    destroying it.

    Examples
    --------
    >>> for ln in wrap_structured_line("- alpha beta gamma delta", 16):
    ...     print(repr(ln))
    '- alpha beta'
    '  gamma delta'
    >>> wrap_structured_line("| a | b |", 4)
    ['| a | b |']
    """
    stripped = line.strip()
    if len(stripped) <= width:
        # Already fits: return it byte-identical. Re-flowing a line that did
        # not need it would collapse the author's own intra-item alignment —
        # `- floor:   nearest index…` lines its descriptions into a column,
        # and `_wrap` splits on whitespace, so it cannot preserve that. Only
        # an item that overflows pays the cost, which keeps gh-653's promise
        # intact for every list short enough to keep it.
        return [line]
    if stripped.startswith("|"):
        return [line]
    m = _STRUCTURED_RE.match(stripped)
    if not m:
        return _wrap(stripped, width) or [line]
    hang = " " * len(m.group(0))
    head, *rest = _wrap(stripped, width) or [stripped]
    if not rest:
        return [head]
    return [head] + [
        hang + w for w in _wrap(" ".join(rest), width - len(hang))
    ]


def render_numpy_doc(
    block: DoxyBlock | None,
    name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
    *,
    indent: int = 8,
    skeleton_fallback: bool = False,
    param_fallback: str = "Input.",
    return_fallback: str = "Output.",
    raises: "list[tuple[str, str]] | None" = None,
) -> list[str]:
    """Return `.pyi` numpy-docstring lines for one method or free function.

    **The** renderer for a member docstring (gh-651). Every generator that
    turns a :class:`DoxyBlock` into numpy text routes through here — the
    module-aggregated ``.pyi`` (``_stubs``), a standalone object's methods
    (``_context/_methods``), free functions, and ``jm handle`` methods — so
    the layout cannot drift between them. It previously had two hand-written
    implementations that disagreed on three things at once: whether the
    extended description appeared at all, whether a ``Parameters`` entry
    carried its type, and whether the blank line between sections was blank
    or eight spaces.

    Parameters
    ----------
    block : DoxyBlock or None
        Parsed header comment; ``None`` when the header documents nothing.
    name : str
        Member name, used for the summary fallback.
    py_params : list of tuple(str, str)
        ``(name, annotation)`` for the Python-facing arguments to document,
        in order. Only these appear in ``Parameters`` — a binding-level
        argument such as ``out=`` is deliberately left out by the caller.
    ret_ann : str
        Python return annotation; ``"None"`` suppresses the ``Returns``
        section entirely.
    override : str, optional
        Manifest ``doc=``, which outranks the header ``@brief``.
    indent : int, optional
        Leading spaces. 8 inside a class, 4 for a module-level function.
    skeleton_fallback : bool, optional
        What to emit when there is nothing to derive from — no *block* and no
        *override*. ``False`` (the default, and what the aggregated ``.pyi``
        has always done) collapses to the one-line name stub. ``True`` keeps
        the full section skeleton with ``Input.``/``Output.`` placeholders,
        which is what a standalone object's ``.pyi`` has always done.

        The two paths genuinely disagree here, and the disagreement is about
        policy rather than layout, so it stays a caller's choice rather than
        being silently unified. Worth settling separately.
    raises : list of tuple(str, str), optional
        ``(exception_class, description)`` the *manifest* declares this member
        raises, rendered as a numpy ``Raises`` section (gh-869). Distinct from
        a header ``@throws``, which the block carries and which is merged with
        these; both end up in the one section.

    Returns
    -------
    list of str
        Complete docstring lines, opening and closing ``\"\"\"`` included.
    """
    pad = " " * indent
    if block is None and not override and not skeleton_fallback and not raises:
        return [f'{pad}"""{name.replace("_", " ").capitalize()}."""']

    lines, examples = _numpy_sections(
        block,
        name,
        py_params,
        ret_ann,
        override,
        skeleton_fallback=skeleton_fallback,
        param_fallback=param_fallback,
        return_fallback=return_fallback,
        raises=raises,
    )
    # gh-652: a backslash in a plain triple-quoted string is an invalid escape
    # sequence — `\l` in `:math:`20\log_{10}(g)`` is the common case — and
    # that is a SyntaxWarning on 3.12+ *in the generated project*. Emitting the
    # docstring raw fixes it while preserving the markup for a docs build;
    # escaping would render `\\log`. Only docstrings containing a backslash
    # change, so this is invisible everywhere else.
    # gh-877: a skeleton with nothing to put in it collapses to the one-line
    # form. The renderer omits a section it cannot fill, so a member with no
    # parameters and no return value (`reset`) reaches here with the summary
    # alone — and the two-line spelling of that summary carries exactly as much
    # information as the one-line spelling.
    #
    # That matters because `jm status --check` compares byte-for-byte: emitting
    # the long form would report drift in every existing project, for every
    # such member, in exchange for a newline. "Skeleton everywhere" is a rule
    # about what gets *documented*, not a licence to reformat what does not.
    # `override` is deliberately NOT part of this condition: a built-in always
    # supplies its canned summary as the override, so requiring its absence
    # would mean this never fires for the members it exists for.
    if (
        block is None
        and skeleton_fallback
        and not raises
        and not examples
        and len(lines) == 1
    ):
        return [f'{pad}"""{lines[0]}"""']

    quote = 'r"""' if needs_raw_string(lines + examples) else '"""'
    out = [f"{pad}{quote}{lines[0]}"]
    # Blank separators must stay genuinely blank: `pad` on an empty line is
    # trailing whitespace, which is what one of the two pre-gh-651 renderers
    # used to emit (eight spaces) and the other did not.
    out += [f"{pad}{ln}".rstrip() for ln in lines[1:]]
    if examples:  # @code ... @endcode -> runnable doctest
        out += ["", f"{pad}Examples", f"{pad}--------"]
        out += [f"{pad}{ex}".rstrip() for ex in examples]
        # Trailing blank: under pytest --doctest-glob the .pyi is parsed as a
        # text file, where expected output runs until a blank line — without
        # this the closing `"""` is swallowed into the last example's output.
        out.append("")
    out.append(f'{pad}"""')
    return out


# gh-652: where each quarantined Doxygen block tag lands in numpy's standard.
# Largely a mapping table rather than a design problem — numpydoc already has a
# section for nearly every one. Two calls are worth stating outright:
#
# * ``@pre``/``@post``/``@invariant`` go to ``Notes``, not a section of their
#   own. numpydoc has no precondition section, and inventing one puts
#   non-standard headings into every downstream docs build.
# * ``@retval`` merges into ``Returns`` (handled separately below), because a
#   C function returning 0/-1 becomes a Python method that raises or returns —
#   the per-value rows still read correctly there and have no other home.
_TAG_SECTION = {
    "note": "Notes",
    "attention": "Notes",
    "remark": "Notes",
    "remarks": "Notes",
    "pre": "Notes",
    "post": "Notes",
    "invariant": "Notes",
    "par": "Notes",
    "warning": "Warnings",
    "see": "See Also",
    "sa": "See Also",
    "throws": "Raises",
    "exception": "Raises",
}

# C-side metadata with no Python meaning. Dropped deliberately, and named here
# so "dropped" is a decision a reader can audit rather than an omission.
_TAG_DROPPED = frozenset(
    {
        "todo",
        "bug",
        "since",
        "version",
        "ingroup",
        "tparam",
        "copydoc",
        "copybrief",
        "file",
        "author",
    }
)

# numpydoc's section order. Anything jm emits must follow it, or tooling that
# parses by section (griffe, numpydoc's own validator) mis-associates the body.
#
# gh-805 §F: ``Warns`` sits between ``Raises`` and ``See Also`` in numpydoc's
# standard, and it is not the same section as ``Warnings`` further down —
# ``Warns`` is the structured list of warning *categories* a call may emit,
# ``Warnings`` is free cautionary prose. Both appear here because jm emits
# both, from different inputs, and the near-identical names are exactly the
# pair a future edit would collapse.
_SECTION_ORDER = ("Raises", "Warns", "See Also", "Notes", "Warnings")

#: Sections whose entries are ``(type, description)`` rather than free prose:
#: the type alone on its line, the description indented under it. numpydoc
#: renders `Raises` and `Warns` identically, so they share one layout.
TYPED_SECTIONS = ("Raises", "Warns")


def typed_section(
    heading: str,
    entries: "Sequence[tuple[str, str]]",
    desc_width: int,
    fallback: str = "Raised.",
) -> list[str]:
    """Lay out a ``Raises``/``Warns`` section, unindented.

    numpydoc wants the exception or warning class on a line of its own with an
    indented description beneath — not one run-on line. Both faces need that
    layout and both used to be candidates for writing it out: the method face
    had it inline in `_numpy_sections`, and gh-805 §F needed the same thing for
    the class docstring, at a different width. A second copy is how the two
    would come to disagree about the fallback text or the hanging indent, so
    the layout lives here and the callers supply content and width.

    Parameters
    ----------
    heading : str
        ``"Raises"`` or ``"Warns"``. Underlined to its own length.
    entries : sequence of tuple(str, str)
        ``(class_name, description)`` pairs, in the order they should read.
    desc_width : int
        Column budget for the wrapped description, already net of its own
        four-space hanging indent. The class face and the member face differ
        here because their base indents differ.
    fallback : str, optional
        Description for an entry that carries none.

    Returns
    -------
    list of str
        Lines with no base indent, opening with a blank separator so the
        caller can splice without tracking whether one is needed.

    Examples
    --------
    >>> typed_section("Raises", [("ValueError", "If n < 0.")], 60)
    ['', 'Raises', '------', 'ValueError', '    If n < 0.']
    >>> typed_section("Warns", [("UserWarning", "")], 60, "Emitted.")
    ['', 'Warns', '-----', 'UserWarning', '    Emitted.']
    """
    out = ["", heading, "-" * len(heading)]
    for cls, desc in entries:
        out.append(cls)
        out += [
            f"    {w}" for w in _wrap(desc.strip() or fallback, desc_width)
        ]
    return out


# ``@f$ ... @f$`` is Doxygen's inline math. Mapped to reST ``:math:`` so a docs
# build renders it — and, just as importantly, so the backslashes inside it are
# handled deliberately rather than landing in a plain Python string literal
# (see `_needs_raw_string`).
_MATH_RE = re.compile(r"[@\\]f\$(.+?)[@\\]f\$", re.DOTALL)


def _map_math(text: str) -> str:
    """Rewrite Doxygen inline math to reST ``:math:`` (gh-652).

    >>> _map_math("Gain in dB: @f$ 20\\\\log_{10}(g) @f$.")
    'Gain in dB: :math:`20\\\\log_{10}(g)`.'
    """
    return _MATH_RE.sub(lambda m: f":math:`{m.group(1).strip()}`", text)


def _clean(text: str) -> str:
    """Inline-marker strip plus math mapping, applied to every parsed field."""
    return _map_math(_strip_doxy_inline(text))


def needs_raw_string(lines: list[str]) -> bool:
    """True when a rendered docstring must be emitted as ``r\"\"\"``.

    A backslash reaching a ``.pyi``'s plain triple-quoted string is an invalid
    escape sequence — ``\\l`` in ``:math:`20\\log_{10}(g)``` is the common case
    once ``@f$`` maps through. That is a ``SyntaxWarning`` on 3.12+ **in the
    generated project**, not in jm, and jm supports 3.9 through 3.14, so it
    would be visible to downstream users long before it became an error.

    Emitting the stub docstring raw is the fix that also preserves the markup
    for a docs build; escaping the backslash would render ``\\\\log``. Only
    docstrings that actually contain a backslash change, so this is invisible
    everywhere else.

    A raw string cannot end in a backslash, so a trailing one disqualifies —
    the caller escapes instead, which is correct because a docstring ending in
    a lone backslash has no markup meaning to preserve.
    """
    if not any("\\" in ln for ln in lines):
        return False
    return not lines[-1].rstrip().endswith("\\")


def _tag_sections(block: DoxyBlock) -> tuple[dict[str, list[str]], list[str]]:
    """Group a block's quarantined tags into numpy sections (gh-652).

    Returns
    -------
    tuple
        ``(sections, retvals)`` — ``sections`` maps a numpy heading to its
        entry lines, ``retvals`` are the ``@retval`` rows the caller folds
        into ``Returns``.
    """
    sections: dict[str, list[str]] = {}
    retvals: list[str] = []
    for cmd, raw in block.tags:
        text = raw.strip()  # already cleaned at parse time
        if not text or cmd in _TAG_DROPPED:
            continue
        if cmd == "retval":
            retvals.append(text)
            continue
        if cmd == "deprecated":
            # reST directive rather than a section: numpydoc renders it as an
            # admonition wherever it appears, and it reads as part of the
            # description rather than as trailing metadata.
            sections.setdefault("__deprecated__", []).append(text)
            continue
        dest = _TAG_SECTION.get(cmd)
        if dest is None:
            continue  # unrecognised -> still quarantined, still dropped
        sections.setdefault(dest, []).append(text)
    return sections, retvals


def _numpy_sections(
    block: DoxyBlock | None,
    name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
    *,
    skeleton_fallback: bool = False,
    param_fallback: str = "Input.",
    return_fallback: str = "Output.",
    raises: "list[tuple[str, str]] | None" = None,
) -> tuple[list[str], list[str]]:
    """Unindented numpy section lines, with ``Examples`` kept separate.

    The shared core of both faces (gh-642). The ``.pyi`` face
    (:func:`render_numpy_doc`) indents these, wraps them in ``\"\"\"`` and
    appends the ``Examples`` block; the runtime face
    (:func:`render_runtime_doc`) takes them as-is and drops ``Examples``.
    Keeping ``Examples`` out of the returned lines is what lets the runtime
    face discard it without having to recognise where the section starts.

    Returns
    -------
    tuple
        ``(section_lines, example_lines)``. ``section_lines[0]`` is the
        summary, never blank — the caller splices it onto its own opening
        delimiter.
    """
    if block is not None:
        summary, body, descs, ret, examples = render_numpy_method_doc(
            block, py_params
        )
    else:
        summary, body, descs, ret, examples = "", [], {}, "", []
    summary = override or summary
    if not summary:
        # gh-867: capitalised on BOTH faces. `skeleton_fallback` used to
        # decide this too, so one flag answered two questions -- "emit the
        # section skeleton?" and "how is a name-derived summary spelled?" --
        # and the answers had no reason to travel together. The cost was that
        # the same undocumented member read `Close.` in a module-aggregated
        # stub and `close.` in a standalone one, and `steps.` at runtime.
        #
        # A capitalised sentence is also simply the right answer: numpydoc
        # wants the summary capitalised and terminated, and gh-685 already
        # pinned that spelling as a deliberate guarantee for views. The flag
        # now controls the skeleton alone, which is what its name says.
        summary = name.replace("_", " ").capitalize() + "."
    # gh-652: quarantined block tags become real numpy sections. Rendered here
    # rather than in either face, so both get them from the one builder.
    tag_secs, retvals = _tag_sections(block) if block is not None else ({}, [])
    # gh-869: an exception the *manifest* declares. A header ``@throws`` is
    # the same section reached from the other direction, so it is merged into
    # the same list rather than rendered beside it — two ``Raises`` headings
    # in one docstring is not a numpy document. Declared entries come last:
    # a header that documents the failure in the author's own words should
    # read first.
    for _cat, _desc in raises or []:
        tag_secs.setdefault("Raises", []).append(f"{_cat} {_desc}")
    # gh-744: the summary wraps like every other section. It used to be the
    # single exception, spliced in at whatever length the header wrote it.
    out = wrap_summary(summary)
    if "__deprecated__" in tag_secs:
        for note in tag_secs.pop("__deprecated__"):
            out += ["", ".. deprecated::"] + [
                f"   {w}" for w in _wrap(note, _DEPRECATED_WIDTH)
            ]
    # `body` arrives already grouped into paragraphs by
    # render_numpy_method_doc, so re-group would be a no-op — wrap only.
    for para in body:
        out.append("")
        # gh-653: a paragraph carrying newlines is a preserved structure — a
        # bullet list or a table — so its line breaks are the author's and
        # `group_paragraphs`'s, not this function's to re-flow. gh-717/gh-744:
        # each *item* is still wrapped within itself, because folding a
        # wrapped continuation back into its bullet makes the bullet longer
        # than the 79 columns everything else is held to. A table row is the
        # one thing left verbatim — see `wrap_structured_line`.
        if "\n" in para:
            for sline in para.split("\n"):
                out += wrap_structured_line(sline, DOC_WIDTH)
        else:
            out += _wrap(para, DOC_WIDTH)
    # gh-678: descriptions wrap on the same rule as the body. They used to be
    # emitted verbatim however long they were, so one docstring could carry a
    # wrapped summary directly above a 110-column parameter description. The
    # continuation indent is four deeper than the section, so the budget is
    # four narrower; `_wrap` still never splits a token, so a lone URL or long
    # identifier overflows rather than being broken.
    if py_params:
        out += ["", "Parameters", "----------"]
        for pname, ann in py_params:
            out.append(f"{pname} : {ann}")
            desc = descs.get(pname) or param_fallback
            out += [f"    {w}" for w in _wrap(desc, DESC_WIDTH)]
    if ret_ann != "None":
        out += ["", "Returns", "-------", ret_ann]
        out += [f"    {w}" for w in _wrap(ret or return_fallback, DESC_WIDTH)]
        # gh-652: `@retval <v> <desc>` rows read as additional Returns entries.
        # A C function returning 0/-1 becomes a Python method that raises or
        # returns a value, and numpy has no other home for the per-value rows.
        for rv in retvals:
            head, _, rest = rv.partition(" ")
            out.append(head)
            out += [
                f"    {w}"
                for w in _wrap(rest.strip() or "Return value.", DESC_WIDTH)
            ]
    for heading in _SECTION_ORDER:
        entries = tag_secs.get(heading)
        if not entries:
            continue
        if heading in TYPED_SECTIONS:
            # numpydoc wants `ExceptionType` then an indented description, not
            # one run-on line. `@throws ValueError if n < 0` carries the type
            # as its first token, which is the convention Doxygen users already
            # follow — so splitting on the first space recovers the pair the
            # shared layout wants.
            out += typed_section(
                heading,
                [(e.partition(" ")[0], e.partition(" ")[2]) for e in entries],
                DESC_WIDTH,
            )
            continue
        out += ["", heading, "-" * len(heading)]
        for i, entry in enumerate(entries):
            # Notes and Warnings are free prose, so consecutive entries must be
            # blank-line separated — otherwise two `@note`s render as one
            # run-on paragraph, which is what the reader would blame on jm.
            if i and heading in ("Notes", "Warnings"):
                out.append("")
            out += _wrap(entry, DOC_WIDTH)
    return out, examples


def render_runtime_doc(
    block: DoxyBlock | None,
    name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
    *,
    param_fallback: str = "Input.",
    return_fallback: str = "Output.",
    raises: "list[tuple[str, str]] | None" = None,
) -> list[str]:
    """Return runtime ``__doc__`` lines for one method, class or property.

    The numpy block the ``.pyi`` already builds, minus only the parts that
    mean nothing outside a stub file: no indentation and no ``\"\"\"``
    delimiters (gh-642). Callers splice the result into a C string literal via
    ``_context._parse._build_ml_doc``, between their signature line and the
    synthesized doctest they already emit.

    Because this shares :func:`_numpy_sections` with the stub face, the
    returned text *is* the stub's text with the indent and delimiters removed.
    That is the invariant, and it is what makes the two faces unable to drift;
    see ``tests/test_gh642_runtime_doc_parity.py``.

    **On ``Examples``.** doppler's answer in doppler-dsp/doppler#568 was to
    leave them out of the runtime face: their coverage meter scores a callable
    runtime-FULL once *an* example is present, the synthesized doctest each
    caller appends already satisfies that, and ``Examples`` is the bulkiest
    section — so on their metric it is pure ``.so`` weight. They are rendered
    anyway, because that metric is not the reason the section exists.
    ``help(obj.method)`` at a REPL is where someone asks "how do I actually
    use this?", and answering it with less than a stub file they will never
    open is the exact complaint gh-642 was filed about. A rule with no
    exceptions is also worth more than the bytes: "the runtime block is the
    stub block" needs no caveat re-derived by every future reader.

    A member whose header carries no ``@code`` is unaffected — the section is
    only emitted when there is something real to put in it, and the
    synthesized doctest stays below this block either way.

    Parameters
    ----------
    block : DoxyBlock or None
        Parsed header comment; ``None`` when the header documents nothing.
    name : str
        Member name, used only for the summary fallback.
    py_params : list of tuple(str, str)
        ``(name, annotation)`` for the documented arguments, in order. Must be
        the *same* list handed to :func:`render_numpy_doc` for this member —
        passing a different one is precisely the drift this exists to prevent.
    ret_ann : str
        Python return annotation; ``"None"`` suppresses ``Returns``.
    override : str, optional
        Summary that outranks the header ``@brief`` — the manifest ``doc=``,
        or the caller's own shape-specific default sentence.

    Returns
    -------
    list of str
        Section lines, summary first, with no trailing blank line.

    Examples
    --------
    >>> blk = DoxyBlock(brief="Filter a block.", returns="Filtered output.")
    >>> render_runtime_doc(blk, "run", [("x", "ndarray")], "ndarray")
    ['Filter a block.', '', 'Parameters', '----------', 'x : ndarray', \
'    Input.', '', 'Returns', '-------', 'ndarray', '    Filtered output.']
    """
    lines, examples = _numpy_sections(
        block,
        name,
        py_params,
        ret_ann,
        override,
        skeleton_fallback=True,
        param_fallback=param_fallback,
        return_fallback=return_fallback,
        raises=raises,
    )
    if examples:  # @code ... @endcode -> runnable doctest
        lines += ["", "Examples", "--------", *(e.rstrip() for e in examples)]
    return lines


def render_numpy_method_doc(
    block: DoxyBlock,
    py_params: list[tuple[str, str]],
) -> tuple[str, list[str], dict[str, str], str, list[str]]:
    """Resolve a method's prose fields from *block* for numpy rendering.

    This returns the *pieces* (not a fully formatted docstring) so each
    generator can splice them into its own indentation and combine them with
    the synthesized ``Examples`` block it already builds.

    Parameters
    ----------
    block : DoxyBlock
        Parsed header comment for this method.
    py_params : list of tuple(str, str)
        ``(name, type_annotation)`` for the *Python-facing* arguments only,
        in order. Used to filter the Doxygen ``@param`` list down to real
        Python args (drops C-only params like ``state``/``x_len``/``out``/
        ``max_out``) and to align descriptions when names differ.

    Returns
    -------
    tuple
        ``(summary, body_paragraphs, param_desc_by_name, return_desc,
        example_lines)`` where ``body_paragraphs`` is the extended description
        grouped into flowing paragraphs, ``param_desc_by_name`` maps each Python
        arg name to its description (possibly empty), and ``example_lines`` are
        the verbatim ``@code`` doctest lines (empty if none).
    """
    py_names = [n for n, _ in py_params]
    # exact-name matches first
    desc_by_name: dict[str, str] = {}
    matched = set()
    for n in py_names:
        d = block.param_desc(n)
        if d is not None:
            desc_by_name[n] = d
            matched.add(n)
    # positional zip of the remaining (Doxygen names that aren't Python args,
    # e.g. C `samples` -> Python `x`) only when the leftover counts align.
    if len(matched) < len(py_names):
        leftover_py = [n for n in py_names if n not in matched]
        leftover_dox = [d for nm, d in block.params if nm not in py_names]
        if len(leftover_dox) == len(leftover_py):
            for n, d in zip(leftover_py, leftover_dox):
                desc_by_name[n] = d
    for n in py_names:
        desc_by_name.setdefault(n, "")
    return (
        block.brief,
        group_paragraphs(block.body),
        desc_by_name,
        block.returns,
        list(block.examples),
    )
