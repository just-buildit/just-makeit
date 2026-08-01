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
_BLOCK_THEN_DECL_RE = re.compile(
    r"/\*\*(?P<block>(?:(?!\*/)[\s\S])*?)\*/\s*"
    r"(?P<decl>[^;{}/*][^;{}]*?\([^;{}]*\)\s*[;{])",
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
    if brief in scaffold_briefs(member, owner):
        return True
    if brief != _norm_brief(member):
        return False
    # Generic ``@brief <member>.`` — only an untouched skeleton counts.
    return not block.returns.strip() and not any(
        desc.strip() for _n, desc in block.params
    )


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
        brief=_strip_doxy_inline(brief),
        body=[_strip_doxy_inline(b) for b in body_lines],
        params=[(n, _strip_doxy_inline(d)) for n, d in params],
        returns=_strip_doxy_inline(returns),
        examples=example_lines,
        param_dirs=param_dirs,
        tags=[(c, _strip_doxy_inline(t)) for c, t in tags],
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
    pad2 = " " * (indent + 4)
    if block is None and not override and not skeleton_fallback:
        return [f'{pad}"""{name.replace("_", " ").capitalize()}."""']

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
    out = [f'{pad}"""{summary}']
    # `body` arrives already grouped into paragraphs by
    # render_numpy_method_doc, so re-group would be a no-op — wrap only.
    for para in body:
        out.append("")
        out += [f"{pad}{w}" for w in _wrap(para, 72)]
    if py_params:
        out += ["", f"{pad}Parameters", f"{pad}----------"]
        for pname, ann in py_params:
            out.append(f"{pad}{pname} : {ann}")
            out.append(f"{pad2}{descs.get(pname) or 'Input.'}")
    if ret_ann != "None":
        out += [
            "",
            f"{pad}Returns",
            f"{pad}-------",
            f"{pad}{ret_ann}",
            f"{pad2}{ret or 'Output.'}",
        ]
    if examples:  # @code ... @endcode -> runnable doctest
        out += ["", f"{pad}Examples", f"{pad}--------"]
        out += [f"{pad}{ex}".rstrip() for ex in examples]
        # Trailing blank: under pytest --doctest-glob the .pyi is parsed as a
        # text file, where expected output runs until a blank line — without
        # this the closing `"""` is swallowed into the last example's output.
        out.append("")
    out.append(f'{pad}"""')
    return out


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
