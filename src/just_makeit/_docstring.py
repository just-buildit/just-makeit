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


def group_paragraphs(lines: list[str]) -> list[str]:
    """Join a list of body *lines* into paragraphs.

    Consecutive non-blank lines become one space-joined paragraph; blank lines
    separate paragraphs. Used so multi-line Doxygen prose renders as flowing
    paragraphs instead of one short line per source line (which the renderers
    would otherwise blank-line-separate into a double-spaced block).
    """
    paras: list[str] = []
    cur: list[str] = []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
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
_BLOCK_THEN_DECL_RE = re.compile(
    r"/\*\*(?P<block>(?:(?!\*/)[\s\S])*?)\*/\s*"
    r"(?P<decl>[^;{}/*\s][^;{}/]*?\([^;{}/]*\)\s*[;{])",
    re.DOTALL,
)


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
    for m in _BLOCK_THEN_DECL_RE.finditer(header_text):
        name = _decl_name(m.group("decl"))
        if name:
            out[name] = "/**" + m.group("block") + "*/"
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


def _strip_comment(raw: str) -> list[str]:
    """Strip ``/** */`` delimiters and per-line ``*`` prefixes.

    Returns the interior lines with leading decoration removed but otherwise
    preserving relative content (blank lines kept for paragraph splitting).
    """
    body = raw.strip()
    if body.startswith("/**"):
        body = body[3:]
    elif body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines: list[str] = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("*"):
            s = s[1:]
        # drop exactly one leading space left by the "* " decoration
        if s.startswith(" "):
            s = s[1:]
        lines.append(s.rstrip())
    # trim leading/trailing blank lines
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
            body_lines.append(stripped)

    # If there was no @brief tag, the first body paragraph is the brief.
    if not saw_brief_tag and not brief_parts and body_lines:
        first: list[str] = []
        while body_lines and body_lines[0].strip():
            first.append(body_lines.pop(0))
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


def _wrap(text: str, width: int = 70) -> list[str]:
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

    Returns
    -------
    list of str
        Complete docstring lines, opening and closing ``\"\"\"`` included.
    """
    pad = " " * indent
    if block is None and not override and not skeleton_fallback:
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
    )
    # gh-652: a backslash in a plain triple-quoted string is an invalid escape
    # sequence — `\l` in `:math:`20\log_{10}(g)`` is the common case — and
    # that is a SyntaxWarning on 3.12+ *in the generated project*. Emitting the
    # docstring raw fixes it while preserving the markup for a docs build;
    # escaping would render `\\log`. Only docstrings containing a backslash
    # change, so this is invisible everywhere else.
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
_SECTION_ORDER = ("Raises", "See Also", "Notes", "Warnings")

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
        summary = (
            f"{name}."
            if skeleton_fallback
            else name.replace("_", " ").capitalize() + "."
        )
    # gh-652: quarantined block tags become real numpy sections. Rendered here
    # rather than in either face, so both get them from the one builder.
    tag_secs, retvals = _tag_sections(block) if block is not None else ({}, [])
    out = [summary]
    if "__deprecated__" in tag_secs:
        for note in tag_secs.pop("__deprecated__"):
            out += ["", ".. deprecated::"] + [
                f"   {w}" for w in _wrap(note, 69)
            ]
    # `body` arrives already grouped into paragraphs by
    # render_numpy_method_doc, so re-group would be a no-op — wrap only.
    for para in body:
        out.append("")
        out += _wrap(para, 72)
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
            out += [f"    {w}" for w in _wrap(desc, 68)]
    if ret_ann != "None":
        out += ["", "Returns", "-------", ret_ann]
        out += [f"    {w}" for w in _wrap(ret or return_fallback, 68)]
        # gh-652: `@retval <v> <desc>` rows read as additional Returns entries.
        # A C function returning 0/-1 becomes a Python method that raises or
        # returns a value, and numpy has no other home for the per-value rows.
        for rv in retvals:
            head, _, rest = rv.partition(" ")
            out.append(head)
            out += [
                f"    {w}" for w in _wrap(rest.strip() or "Return value.", 68)
            ]
    for heading in _SECTION_ORDER:
        entries = tag_secs.get(heading)
        if not entries:
            continue
        out += ["", heading, "-" * len(heading)]
        for i, entry in enumerate(entries):
            if heading == "Raises":
                # numpydoc wants `ExceptionType` then an indented description,
                # not one run-on line. `@throws ValueError if n < 0` carries
                # the type as its first token, which is the convention Doxygen
                # users already follow.
                exc, _, rest = entry.partition(" ")
                out.append(exc)
                out += [
                    f"    {w}" for w in _wrap(rest.strip() or "Raised.", 68)
                ]
                continue
            # Notes and Warnings are free prose, so consecutive entries must be
            # blank-line separated — otherwise two `@note`s render as one
            # run-on paragraph, which is what the reader would blame on jm.
            if i and heading in ("Notes", "Warnings"):
                out.append("")
            out += _wrap(entry, 72)
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
