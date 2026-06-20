"""
Custom superfences formatter for animated terminal (termynal) blocks.

Registered in mkdocs.yml as:

    custom_fences:
      - name: termynal
        class: termynal
        format: !!python/name:termynal_fence.termynal_fence

Usage in Markdown — inline color markup within any line:

    ```termynal
    $ just-makeit new my_project --object my_object
      create  native/inc/my_object/my_object_core.h
    {g}Done!{/g}  {c}cd my_project && make && make test{/c}
    $ cd my_project && make && make test
    {G}[100%] Linking C shared module my_object.so{/G}
    {g}100% tests passed{/g}, 0 tests failed out of 1
    ```

Color markers (wrap any fragment of a line):

    {g}text{/g}    bold green  — jm "Done!", cmake "Linking", ctest "passed", "OK"
    {G}text{/G}    green       — cmake "Building ..."
    {c}text{/c}    bold cyan   — jm cmd hint, file paths
    {b}text{/b}    bold blue   — cmake "Copy extension module"
    {y}text{/y}    bold yellow — warnings, highlights
    {mark}t{/mark} amber       — installer "–→" pointer, callout arrows
    {d}text{/d}    dim gray    — header/comment lines, separators

Lines starting with ``$ `` animate as typed input.
Lines starting with ``# `` render as comments (no typing animation).
Blank lines insert a small vertical gap.
"""

import html as _html
import re

_COLORS = {
    "g": "ty-green-bold",
    "G": "ty-green",
    "c": "ty-cyan-bold",
    "b": "ty-blue-bold",
    "y": "ty-yellow-bold",
    "mark": "ty-amber",
    "d": "ty-dim",
}

_MARKUP_RE = re.compile(r"\{(g|G|c|b|y|mark|d)\}(.*?)\{/\1\}", re.DOTALL)


def _colorize(text: str) -> str:
    """Convert {g}...{/g} markup to HTML spans; escape the rest."""
    parts: list[str] = []
    pos = 0
    for m in _MARKUP_RE.finditer(text):
        if m.start() > pos:
            parts.append(_html.escape(text[pos : m.start()]))
        cls = _COLORS[m.group(1)]
        inner = _html.escape(m.group(2))
        parts.append(f'<span class="{cls}">{inner}</span>')
        pos = m.end()
    if pos < len(text):
        parts.append(_html.escape(text[pos:]))
    return "".join(parts)


def termynal_fence(source, language, css_class, options, md, **kwargs):
    lines = source.strip().splitlines()
    spans: list[str] = []
    for line in lines:
        if line.startswith("$ "):
            cmd = _colorize(line[2:])
            spans.append(f'<span data-ty="input">{cmd}</span>')
        elif line.startswith("# "):
            comment = _colorize(line)
            spans.append(f'<span data-ty="comment">{comment}</span>')
        elif line == "":
            spans.append('<span data-ty>&nbsp;</span>')
        else:
            out = _colorize(line)
            spans.append(f"<span data-ty>{out}</span>")
    inner = "\n    ".join(spans)
    return (
        '<div class="jm-termy" data-termynal data-ty-macos'
        ' data-ty-typeDelay="40" data-ty-lineDelay="400">\n    '
        f"{inner}\n</div>"
    )
