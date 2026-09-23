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

import io
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

from . import _config as C
from . import _fmtprobe
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


# The last line of a `def` whose whole body is `...`: a one-line stub
# (`def f(self) -> int: ...`) or the closing line of a wrapped one
# (`    ) -> int: ...`).
_STUB_DEF_END_RE = re.compile(r"^[ ]*(?:(?:async\s+)?def\b.*|\).*):\s*\.\.\.$")
_DEF_START_RE = re.compile(r"^[ ]*(?:async\s+)?def\b")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _decorated_def(lines: "list[str]", n: int) -> bool:
    """Whether *lines[n]* starts a ``def``, directly or under decorators.

    A decorated CLASS does not count: ruff keeps its blank line after a stub
    ``def`` (``@final`` over a record type after a module function).
    """
    for line in lines[n:]:
        body = line.lstrip(" ")
        if not body.startswith("@"):
            return body.startswith(("def ", "async def "))
    return False


def stub_blank_lines(lines: "list[str]") -> "list[str]":
    """Lay out the blank lines between a stub's members the way ruff does.

    gh-1478. A ``.pyi`` is jm's alone and ``status --check`` compares it
    byte-for-byte, so a project that runs ``ruff format`` (most do) must find
    it already formatted -- otherwise the formatter rewrites it on the first
    commit and ``status`` calls jm's own file stale (the gh-1432 class).
    Stub files have their own blank-line rules, and the emitters -- eight
    modules concatenating snippets -- each got them slightly wrong: two blank
    lines between methods, one after a ``: ...`` body.

    The rules, each measured against jm's pinned ruff:

    * never more than ONE blank line between two statements, nor at the end;
    * NONE after a ``def`` whose body is ``...`` when the next member at the
      same indent is another ``def``, decorated or not -- a property's
      getter and setter, and a run of ``@property`` stubs, included;
    * exactly one after a class docstring (ruff inserts it before an
      attribute, and keeps it before anything else).

    The interior of a docstring is never touched. Idempotent, like every
    pass here: the output is its own fixed point.

    >>> stub_blank_lines([
    ...     "class A:",
    ...     "    def __init__(self) -> None: ...",
    ...     "",
    ...     "    def f(self) -> int:",
    ...     '        \"\"\"F.\"\"\"',
    ...     "",
    ...     "",
    ...     "    @property",
    ...     "    def g(self) -> int: ...",
    ...     "    @property",
    ...     "    def h(self) -> int: ...",
    ... ])  # doctest: +NORMALIZE_WHITESPACE
    ['class A:',
     '    def __init__(self) -> None: ...',
     '    def f(self) -> int:',
     '        \"\"\"F.\"\"\"',
     '',
     '    @property',
     '    def g(self) -> int: ...',
     '    @property',
     '    def h(self) -> int: ...']
    """
    out: "list[str]" = []
    in_doc = ""
    blanks = 0
    class_doc = False  # the docstring being read is a class's
    after_class_doc = False  # ...and it has just closed
    for n, line in enumerate(lines):
        if in_doc:
            out.append(line)
            if in_doc in line:
                in_doc = ""
                after_class_doc = class_doc
            continue
        if not line.strip():
            blanks += 1
            continue
        prev = len(out) - 1
        keep = min(blanks, 1)
        if after_class_doc:
            keep = 1
        elif prev >= 0 and _STUB_DEF_END_RE.match(out[prev]):
            same_indent = _indent(line) == _indent(out[_def_line(out, prev)])
            if same_indent and _decorated_def(lines, n):
                keep = 0
        if prev >= 0:
            out.extend([""] * keep)
        elif blanks:
            out.extend([""] * blanks)
        blanks = 0
        after_class_doc = False
        header = out[prev].lstrip(" ") if prev >= 0 else ""
        out.append(line)
        is_doc = line.lstrip(" ").lstrip("r").startswith(_TRIPLE)
        class_doc = is_doc and header.startswith("class ") and not keep
        opener = next((q for q in _TRIPLE if line.count(q) % 2), "")
        if opener:
            in_doc = opener
        elif class_doc:
            after_class_doc = True
    # One trailing newline at most: a producer that appended a spacer after
    # its last member left a blank line at the end of the file.
    out.extend([""] * min(blanks, 1))
    return out


def _def_line(lines: "list[str]", end: int) -> int:
    """Index of the ``def`` line of the signature ending at *lines[end]*."""
    i = end
    while i > 0 and not _DEF_START_RE.match(lines[i]):
        i -= 1
    return i


_BLOCK_START = ("def ", "async def ", "class ", "@")


def _continuation_rows(text: str) -> "set[int] | None":
    """The 0-based rows of *text* that continue a logical line.

    Everything after the first physical line of a statement -- the inside
    of a bracket, of a triple-quoted string, of a backslash continuation --
    is the statement's own business, and a layout pass must neither count
    its blank lines nor add any. ``tokenize`` is the one reader that gets
    f-string braces and string brackets right. ``None`` when *text* does not
    tokenize; the caller then leaves it alone.
    """
    rows: "set[int]" = set()
    first = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.NL, tokenize.COMMENT) and first is None:
                continue
            if tok.type in (tokenize.INDENT, tokenize.DEDENT):
                continue
            if tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                if first is not None:
                    rows.update(range(first, tok.end[0]))
                first = None
                continue
            if first is None:
                first = tok.start[0]  # 1-based: the row after it is `first`
    except (tokenize.TokenError, SyntaxError):
        return None
    return rows


def py_blank_lines(text: str) -> str:
    """Lay out the blank lines of a scaffolded ``.py`` file per PEP 8.

    gh-1478. jm's tests and benchmarks are assembled from snippets, each
    carrying its own leading newlines, and whether a slot rendered empty
    decided how many met: top-level functions one blank line apart in the
    pytest face, three blank lines where an empty benchmark block sat, two
    between methods where an optional test was dropped. ``ruff format``
    rewrote every one on a project's first commit.

    A post-pass for the reason :mod:`_pyfmt` gives for stubs: the snippets
    come from dozens of emitters, and one layout rule covers all of them,
    including the next one. The rules are PEP 8's, which every formatter
    agrees on:

    * two blank lines before a top-level ``def``/``class``/decorator (and
      above a comment block sitting directly on one), and before the first
      top-level statement after a function or class body;
    * one before a method, none before the first or after a decorator;
    * none opening a block, at most one inside a body, at most two at
      module level, and one newline at the end of the file.

    Only the first line of a statement is laid out; the inside of a bracket
    or a string is returned as it was. Idempotent.

    >>> src = "import os\\ndef f():\\n\\n    pass\\ndef g():\\n    pass\\n\\n"
    >>> print(py_blank_lines(src), end="")
    import os
    <BLANKLINE>
    <BLANKLINE>
    def f():
        pass
    <BLANKLINE>
    <BLANKLINE>
    def g():
        pass
    >>> py_blank_lines(py_blank_lines(src)) == py_blank_lines(src)
    True
    """
    inside = _continuation_rows(text)
    if inside is None:
        return text
    lines = text.split("\n")
    out: "list[str]" = []
    blanks = 0
    top = ""  # the last statement at column 0
    prev = ""  # the last statement or comment line
    for row, line in enumerate(lines):
        if row in inside:
            out.extend([""] * blanks)
            blanks = 0
            out.append(line)
            continue
        if not line.strip():
            blanks += 1
            continue
        indent = _indent(line)
        body = line.lstrip(" ")
        if not out:
            keep = 0
        elif indent == 0 and _opens_toplevel_block(lines, row):
            keep = 0 if prev.startswith("@") else 2
        elif indent == 0 and _indent(prev) > 0:
            keep = 2
        elif indent == 0:
            keep = min(blanks, 2)
        elif prev.rstrip().endswith(":") and indent > _indent(prev):
            keep = 0
        elif (
            indent == 4
            and top.startswith("class ")
            and body.startswith(_BLOCK_START)
        ):
            keep = 0 if prev.lstrip().startswith("@") else 1
        else:
            keep = min(blanks, 1)
        out.extend([""] * keep)
        out.append(line)
        blanks = 0
        prev = line
        if indent == 0 and not body.startswith(("#", "@")):
            top = body
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def _opens_toplevel_block(lines: "list[str]", i: int) -> bool:
    """Whether column-0 *lines[i]* begins a ``def``/``class``.

    A decorator does, and so does a comment sitting directly on one (no
    blank line between): PEP 8's two blank lines go above the comment.
    """
    for line in lines[i:]:
        if not line.strip() or _indent(line):
            return False
        if line.startswith(_BLOCK_START):
            return True
        if not line.startswith("#"):
            return False
    return False


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
        lines, overlong one-line docstrings wrapped, and the blank lines
        between members laid out by :func:`stub_blank_lines`. The interior
        of a multi-line docstring is returned untouched.
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
    return "\n".join(stub_blank_lines(out))


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


def generated_py_files(root: Path) -> list[Path]:
    """The Python files jm owns outright — the only ones safe to reformat.

    Only ``.pyi`` stubs. They are jm's alone, full-stop: unlike ``_core.c``
    there is no sacred-fragment mechanism, and ``_status`` treats a symbol
    that vanishes from one as content loss precisely because nothing
    hand-written is supposed to live there.

    **A package ``__init__.py`` is deliberately excluded**, even though
    gh-746 asks for the re-export shims. ``apply`` *merges* those files
    rather than overwriting them — ``_merge_module_init`` keeps whatever the
    author added alongside the generated re-exports — so they are hybrid, and
    reformatting a hybrid file rewrites hand-written Python. That is the
    constraint gh-746 itself sets ("never hand-owned Python"), and it is the
    same reasoning that keeps ``_cfmt`` off ``native/inc/**``.

    gh-1432 adds the **generated invariants tests**, which meet that bar
    for the same reason: `_invariants.write` overwrites them whole, they
    carry a DO NOT EDIT banner, and `jm status --check` drift-gates them.
    Being outside this pass is what made a three-newline tail into a
    tug-of-war -- ruff and `end-of-file-fixer` trimmed it, the bytes stopped
    matching the render, and the drift gate went red on a file nobody had
    touched. A downstream's workaround was to exclude the file from ruff
    entirely, which switched off the lint that had just caught an `F821` in
    it.

    The suffix is jm's own and reserved; the user-owned sibling is
    ``test_<comp>.py``. A hand-written file that chose this exact name
    would be reformatted, which is the same bargain the ``.pyi`` glob makes.

    Sorted for a stable invocation order.
    """
    src = root / "src"
    if not src.is_dir():
        return []
    return sorted([*src.rglob("*.pyi"), *src.rglob("test_*_invariants.py")])


def format_project(root: Path, cfg: dict, *, quiet: bool = False) -> None:
    """Run the project's own Python formatter over the generated stubs.

    gh-746. No-op unless ``[project] py_format_command`` is declared. The
    command is appended with the stub paths and run; the project's own
    configuration (``pyproject.toml``'s ``[tool.ruff]``, or whatever the
    command resolves to) decides the layout.

    **Why this is jm's to run rather than the project's.** A ``.pyi`` is
    drift-gated: ``jm status --check`` re-renders and compares byte-for-byte.
    A formatter run *outside* jm therefore reads as permanent drift — the
    project formats the file, jm regenerates it unformatted, and no number of
    ``apply`` runs converges. That is gh-635 exactly, one language over. Once
    jm runs the formatter itself, on **both** the real tree and the throwaway
    scaffold ``apply`` compares against, the two sides are formatted by the
    same command and compare equal.

    That symmetry is the whole design, and it is why jm's own emission does
    not need to be a fixed point of the formatter: the *formatted* output is,
    because formatters are idempotent. A missing binary is a soft failure for
    the same reason — neither side gets formatted, so both stay jm-style and
    still compare equal.
    """
    command = C.py_format_command(cfg)
    if not command:
        return

    # Only argv[0] is resolved; the rest are that program's own arguments —
    # `uv run … ruff format` must resolve `uv`, since `ruff` may well not be
    # on PATH at all in the pinned setup this exists to support.
    if shutil.which(command[0]) is None:
        print(
            f"WARNING: [project] py_format_command names {command[0]!r}, "
            "which was not found on PATH;\n  generated Python keeps jm's "
            "default layout. Install it or fix the command.",
            file=sys.stderr,
        )
        return

    files = generated_py_files(root)
    if not files:
        return

    proc = subprocess.run(
        [*command, *(str(f) for f in files)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        print(
            f"WARNING: {' '.join(command)} failed; generated Python left "
            f"unformatted.\n  {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return

    if not quiet:
        n = len(files)
        print(f"  format  {n} stub{'s' if n != 1 else ''} (py_format_command)")


def format_version(cfg: dict, cwd: "Path | None" = None) -> str:
    """The Python formatter's own ``--version`` output, or ``""``.

    gh-772. The C side has had this since gh-745 and the Python side had
    nothing, so "stale in CI, clean locally" on a ``.pyi`` had no way to name
    its own cause.
    """
    return _fmtprobe.command_version(C.py_format_command(cfg), cwd=cwd)


def cwd_dependent(root: Path, cfg: dict) -> "_fmtprobe.CwdDependence | None":
    """Whether ``py_format_command`` means the same thing from any directory.

    gh-772. jm formats its temp scaffold from outside the project and the real
    tree from inside it, so a CWD-dependent command formats the two compared
    sides differently — the exact exposure gh-758 fixed for C, with no
    detection on this side.

    doppler's is ``["uv", "run", "--group", "dev", "ruff", "format"]``, and
    outside a project it does not resolve a *different* ruff — it fails
    outright with ``Failed to spawn: ruff``. That is milder than the C case
    (the generated Python is simply not formatted on one side, rather than
    formatted differently) and it is the case gh-758's check returned "fine"
    for, which is why `_fmtprobe` reports it as its own kind.
    """
    return _fmtprobe.cwd_dependence(root, C.py_format_command(cfg))
