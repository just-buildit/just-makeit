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
    """

    brief: str = ""
    body: list[str] = field(default_factory=list)
    params: list[tuple[str, str]] = field(default_factory=list)
    returns: str = ""
    examples: list[str] = field(default_factory=list)

    def param_desc(self, name: str) -> str | None:
        """Return the description for parameter *name*, or ``None``."""
        for pname, desc in self.params:
            if pname == name:
                return desc
        return None


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


_TAG_RE = re.compile(r"^@(brief|param|return|returns)\b\s*(.*)$")
_PARAM_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$")


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
    return_parts: list[str] = []
    example_lines: list[str] = []
    # current accumulation target: "brief" | "body" | "param" | "return"
    target = "brief"
    saw_brief_tag = False
    in_code = False

    for ln in lines:
        stripped = ln.strip()
        # @code ... @endcode delimits a verbatim doctest example. Capture the
        # interior lines (the `* ` decoration is already stripped) so they
        # render into a numpy ``Examples`` block / runnable doctest.
        if in_code:
            if stripped.startswith("@endcode"):
                in_code = False
                target = "body"
            else:
                example_lines.append(ln)
            continue
        if stripped.startswith("@code"):
            in_code = True
            continue

        tag_m = _TAG_RE.match(ln)
        if tag_m:
            tag, rest = tag_m.group(1), tag_m.group(2).strip()
            if tag == "brief":
                target, saw_brief_tag = "brief", True
                if rest:
                    brief_parts.append(rest)
            elif tag == "param":
                pm = _PARAM_RE.match(rest)
                if pm:
                    params.append([pm.group(1), pm.group(2).strip()])
                    target = "param"
                else:
                    target = "body"  # malformed @param: ignore tag, keep text
            else:  # return / returns
                target = "return"
                if rest:
                    return_parts.append(rest)
            continue

        if not ln.strip():
            # blank line ends the brief (the summary is a single paragraph,
            # whether tagged @brief or untagged lead text) and separates body
            # paragraphs.
            if target == "brief" and brief_parts:
                target = "body"
            if target == "body" and body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue

        if target == "brief":
            brief_parts.append(ln.strip())
        elif target == "param":
            params[-1][1] = (params[-1][1] + " " + ln.strip()).strip()
        elif target == "return":
            return_parts.append(ln.strip())
        else:
            body_lines.append(ln.strip())

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
    block = DoxyBlock(
        brief=brief,
        body=body_lines,
        params=[(n, d) for n, d in params],
        returns=returns,
        examples=example_lines,
    )

    if not (brief or block.body or block.params or returns or example_lines):
        return None
    # Trivial scaffold brief (e.g. "@brief myverb.") with nothing else.
    if (
        name
        and not block.body
        and not block.params
        and not returns
        and not example_lines
    ):
        norm = brief.rstrip(".").replace("_", " ").strip().lower()
        if norm == name.replace("_", " ").strip().lower():
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
