"""_pyfmt.py — hold generated Python to the project's column target.

The Python counterpart to :mod:`_cfmt`, and deliberately not the same shape.
``_cfmt`` shells out to ``clang-format``; this does the reflow itself, because
a generated ``.pyi`` is compared byte-for-byte by ``jm status --check`` and a
gate whose answer depends on which formatter happens to be installed is not a
gate. Everything here is pure text, deterministic, and idempotent.

Why a post-pass rather than 63 careful call sites
-------------------------------------------------
gh-744 asked for generated signatures to be budgeted to 79 columns. Those
signatures are emitted from **63** f-strings spread across eight modules --
``_stubs``, ``_context/_methods``, ``_record``, ``_codec``, ``_capsule``,
``_composer``, ``_context/_destroy``, ``_handle`` -- each assembling its
parameter list a slightly different way. Teaching all 63 to wrap would be 63
chances to get it wrong and would still not cover the 64th written next month.

A ``.pyi`` is valid Python, so reflowing one afterwards is a mechanical
transform on a small, well-defined grammar: a ``def`` or ``class`` header
whose bracketed list can be broken at its top-level commas. One
implementation, every emitter covered, and a new emitter is covered the day
it is written.

What is deliberately *not* touched
----------------------------------
* **The interior of a multi-line docstring.** That prose is wrapped at its
  source by ``_docstring``, and a doctest line (``>>> obj = Thing(a, b)``)
  must survive byte-identical or the example stops running. Triple-quote
  state is tracked for exactly this reason. A *complete one-line* docstring
  is different -- it has no structure to preserve, and jm hard-codes several
  -- so those are reflowed; see :func:`reflow_oneline_docstring`.
* **Lines already within the target.** The overwhelming majority of generated
  signatures fit, and reflowing them anyway would churn every existing
  project's diff to no purpose.
* **A list with no top-level comma to break at.** ``def f(self) -> Literal[
  "a", "b", ...]:`` has one parameter; splitting inside the annotation would
  be a guess about what reads well, so it is left long and reported by the
  gate instead of being silently mangled.
"""

from __future__ import annotations

import re

from ._docstring import (
    STUB_TARGET_WIDTH,
    summary_docstring,
    wrap_summary,
)

# A `def`/`class` header, captured as (indent, head-up-to-open-bracket,
# body, tail-after-close-bracket). Applied only to a single physical line that
# already exceeds the target, so there is no risk of matching a header that a
# previous run already broke across lines.
_HEADER_RE = re.compile(
    r"^(?P<indent>[ ]*)"
    r"(?P<head>(?:async\s+)?(?:def|class)\s+[A-Za-z_]\w*\s*\()"
    r"(?P<body>.*)"
    r"(?P<tail>\)\s*(?:->.*)?:(?:\s*\.\.\.)?)$"
)

_TRIPLE = ('"""', "'''")

# A complete one-line docstring: `        """Text."""`, optionally raw. Used
# only on a line already over the target, and only when the body carries no
# further triple-quote, so there is nothing to mis-split.
_ONELINE_DOC_RE = re.compile(
    r'^(?P<indent>[ ]*)(?P<raw>r?)"""(?P<body>.*)"""$'
)


# A docstring's opening line with text on it: `    """Summary...`. The body
# must carry no second triple-quote, or the docstring closed on the same line
# and `reflow_oneline_docstring` owns it instead.
_DOC_OPEN_RE = re.compile(r'^(?P<indent>[ ]*)(?P<raw>r?)"""(?P<body>.+)$')


def _split_top_level(body: str) -> list[str]:
    """Split *body* on commas that are not nested inside brackets or strings.

    ``"a: int, b: dict[str, int] = {}"`` splits into two parts, not three --
    the comma inside ``dict[str, int]`` is at depth 1 and the one inside a
    string literal is not a separator at all.

    Returns an empty list when there is nothing to split on, which is the
    caller's signal to leave the line alone.

    Examples
    --------
    >>> _split_top_level("a: int, b: str = ...")
    ['a: int', 'b: str = ...']
    >>> _split_top_level("x: dict[str, int]")
    []
    >>> _split_top_level('s: str = ","')
    []
    """
    parts: list[str] = []
    depth = 0
    quote = ""
    cur = ""
    for ch in body:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts if len(parts) > 1 else []


def reflow_line(line: str, width: int = STUB_TARGET_WIDTH) -> list[str]:
    """Break one overlong ``def``/``class`` header across lines.

    Returns ``[line]`` unchanged when the line fits, is not a header, or has
    no top-level comma to break at -- so the caller can map this over every
    line without first classifying them.

    Examples
    --------
    >>> sig = "    def f(self, a: int, b: int) -> None: ..."
    >>> for ln in reflow_line(sig, 30):
    ...     print(ln)
        def f(
            self,
            a: int,
            b: int,
        ) -> None: ...
    >>> reflow_line("    def f(self) -> None: ...", 30)
    ['    def f(self) -> None: ...']
    """
    if len(line) <= width:
        return [line]
    m = _HEADER_RE.match(line)
    if not m:
        return [line]
    parts = _split_top_level(m["body"])
    if not parts:
        return [line]
    ind = m["indent"]
    inner = ind + "    "
    return (
        [ind + m["head"]] + [f"{inner}{p}," for p in parts] + [ind + m["tail"]]
    )


def reflow_oneline_docstring(
    line: str, width: int = STUB_TARGET_WIDTH
) -> list[str]:
    """Break an overlong single-line docstring into a wrapped block.

    The same argument as :func:`reflow_line`, for the same reason. Most
    docstrings are wrapped at their source by ``_docstring``, but jm also
    emits a handful as fixed literals -- ``"Process a samples array. Returns
    ndarray, or fills out= if supplied."`` is 82 columns at an 8-space indent
    -- and those are spread across ``_context/_sample``, ``_context/_step``
    and ``_gluedoc``. Handling them here covers the ones written next month
    too, rather than pinning each literal to a length its author has to
    remember.

    Returns ``[line]`` unchanged when the line fits or is not a complete
    one-line docstring.

    Examples
    --------
    >>> doc = '  \"\"\"' + 'ab ' * 8 + '.\"\"\"'
    >>> for ln in reflow_oneline_docstring(doc, 24):
    ...     print(ln)
      \"\"\"ab ab ab ab ab ab
      ab ab .
      \"\"\"
    """
    if len(line) <= width:
        return [line]
    m = _ONELINE_DOC_RE.match(line)
    if not m or '"""' in m["body"]:
        return [line]
    block = summary_docstring(m["body"], indent=len(m["indent"]), width=width)
    if m["raw"]:  # keep the r-prefix on the reopened delimiter
        block[0] = block[0].replace('"""', 'r"""', 1)
    return block


def reflow_docstring_open(
    line: str, width: int = STUB_TARGET_WIDTH
) -> list[str]:
    """Wrap the summary on a docstring's opening line.

    The summary is wrapped at its source by ``_docstring.wrap_summary``, but
    not every ``.pyi`` docstring is *rendered* there. ``_context/_step``
    builds the ``step()``/``steps()`` blocks per I/O shape and then splices the
    header's ``@brief`` over the summary line (``_swap_pyi_summary``, gh-676),
    which reintroduced the unwrapped line for exactly the members whose briefs
    are longest -- 19 of them in doppler, up to 234 columns.

    Catching it here rather than in that one splice is the same trade as
    everywhere else in this module: the next renderer to splice a summary is
    covered without knowing this function exists. Source-level wrapping still
    matters where it feeds the *runtime* face too, since a post-pass over the
    ``.pyi`` cannot reach a C string literal.

    Returns ``[line]`` unchanged when the line fits or does not open a
    docstring with text on it.

    Examples
    --------
    >>> open_line = '    \"\"\"' + 'word ' * 20 + 'end.'
    >>> block = reflow_docstring_open(open_line, 40)
    >>> max(len(ln) for ln in block) <= 40
    True
    >>> block[0].startswith('    \"\"\"word')
    True
    """
    if len(line) <= width:
        return [line]
    m = _DOC_OPEN_RE.match(line)
    if not m or '"""' in m["body"]:
        return [line]
    indent = m["indent"]
    wrapped = wrap_summary(m["body"].rstrip(), width - len(indent))
    return [f'{indent}{m["raw"]}"""{wrapped[0]}'] + [
        f"{indent}{w}" for w in wrapped[1:]
    ]


def reflow_pyi(text: str, width: int = STUB_TARGET_WIDTH) -> str:
    """Reflow every overlong signature and one-line docstring in a stub.

    Idempotent: a second pass finds nothing left over *width* to break, so
    ``reflow_pyi(reflow_pyi(t)) == reflow_pyi(t)``. That matters because
    ``jm status --check`` re-renders and compares, and a transform that kept
    moving would report permanent drift (the shape of gh-635).

    Parameters
    ----------
    text : str
        Complete ``.pyi`` source.
    width : int, optional
        Column target; defaults to the project-wide 79.

    Returns
    -------
    str
        The same source with overlong ``def``/``class`` headers broken across
        lines and overlong one-line docstrings wrapped. The interior of a
        multi-line docstring is returned untouched.
    """
    out: list[str] = []
    in_doc = ""
    for line in text.split("\n"):
        if in_doc:
            out.append(line)
            if in_doc in line:
                in_doc = ""
            continue
        # An odd number of a triple-quote on one line is what leaves a
        # docstring hanging open; an even number opened and closed it again,
        # which needs no state and is never a signature either way.
        opener = next((q for q in _TRIPLE if line.count(q) % 2), "")
        if opener:
            out.extend(reflow_docstring_open(line, width))
            in_doc = opener
            continue
        if len(line) > width:
            doc = reflow_oneline_docstring(line, width)
            if doc != [line]:
                out.extend(doc)
                continue
        out.extend(reflow_line(line, width))
    return "\n".join(out)


def flatten_prose(text: str) -> str:
    """Collapse all runs of whitespace so wrapped prose matches flat prose.

    The prose counterpart to :func:`flatten_signatures`, and needed for the
    same reason: gh-744 wraps a docstring summary that does not fit, so a test
    asserting ``summary in pyi`` against the sentence as its author wrote it
    would now be asserting where the line breaks. Normalising both sides keeps
    the assertion about the words.

    Examples
    --------
    >>> flatten_prose("a  wrapped\\n    sentence.")
    'a wrapped sentence.'
    """
    return " ".join(text.split())


def flatten_signatures(pyi: str) -> str:
    """Rejoin multi-line ``def``/``class`` headers into single lines.

    gh-744 budgets generated signatures to 79 columns, so a signature that
    does not fit is now emitted across several lines. Dozens of tests assert
    on a signature by substring, and pinning either the one-line or the
    wrapped form makes them assert the *formatting* rather than the thing they
    care about -- which parameters appear, in which order, with which
    annotations.

    Flattening first keeps those assertions about the signature. A header
    already on one line is returned untouched, so a test that passes today
    goes on passing whether or not its signature later crosses the limit.

    Examples
    --------
    >>> src = "    def f(\\n        self,\\n        a: int,\\n    ) -> None: ..."
    >>> flatten_signatures(src)
    '    def f(self, a: int) -> None: ...'
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in pyi.split("\n"):
        if not buf:
            stripped = line.lstrip()
            starts = stripped.startswith(("def ", "async def ", "class "))
            if starts and line.count("(") > line.count(")"):
                buf = [line.rstrip()]
                depth = line.count("(") - line.count(")")
                continue
            out.append(line)
            continue
        depth += line.count("(") - line.count(")")
        buf.append(line.strip())
        if depth <= 0:
            head = buf[0]
            inner = " ".join(buf[1:-1]).rstrip(",")
            out.append(f"{head}{inner}{buf[-1]}")
            buf = []
    out.extend(buf)
    return "\n".join(out)
