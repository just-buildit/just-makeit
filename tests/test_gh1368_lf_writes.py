"""Every file jm writes goes through ``_textio.write_text`` (gh-1368).

``Path.write_text`` translates ``\\n`` to the platform line ending, so on
Windows every generated file came out CRLF, and the first clang-cl run failed
cmake-lint's ``C0327 Wrong line ending (windows)`` on generated projects. On
Linux and macOS the two spellings produce the same bytes, so no behavioural
test on those hosts can see a regression: CPython fixes the translation when
it is built, not from ``os.linesep`` at runtime. The rule is therefore held
where it can fail on any host, in the source.

Registration-free: the walk covers every module in the package, so a new file
or a new call site is checked without being listed. The bundled examples and
the templates are left out on purpose -- they are user code and generated
code, not jm writing files.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).parent.parent / "src" / "just_makeit"


def _modules() -> "list[Path]":
    return [
        p
        for p in sorted(PKG.rglob("*.py"))
        if not {"examples", "templates"} & set(p.relative_to(PKG).parts)
        and p.name != "_textio.py"
    ]


def _violations(path: Path) -> "list[str]":
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "write_text":
            if isinstance(f.value, ast.Name) and f.value.id == "_textio":
                continue
            out.append(f"{path.name}:{n.lineno}: .write_text(")
        if isinstance(f, ast.Name) and f.id == "open":
            mode = n.args[1] if len(n.args) > 1 else None
            for k in n.keywords:
                if k.arg == "mode":
                    mode = k.value
            m = mode.value if isinstance(mode, ast.Constant) else ""
            writes = isinstance(m, str) and ("w" in m or "a" in m)
            if writes and "b" not in m:
                if not any(k.arg == "newline" for k in n.keywords):
                    out.append(f"{path.name}:{n.lineno}: open({m!r})")
    return out


def test_the_walk_sees_the_package():
    """An empty walk would pass; make sure it reads what it claims to."""
    names = {p.name for p in _modules()}
    assert {"_apply.py", "_init.py", "_config.py"} <= names
    assert len(names) > 50


def test_no_file_is_written_with_a_platform_line_ending():
    bad = [v for p in _modules() for v in _violations(p)]
    assert not bad, (
        "write generated text through `_textio.write_text(path, text)` -- "
        "`Path.write_text` writes CRLF on Windows (gh-1368):\n  "
        + "\n  ".join(bad)
    )


def test_the_check_catches_both_shapes(tmp_path):
    """The detector itself, on the two spellings it must refuse and the ones
    it must allow."""
    src = tmp_path / "m.py"
    src.write_text(
        "from pathlib import Path\n"
        "Path('a').write_text('x', encoding='utf-8')\n"
        "open('b', 'w', encoding='utf-8')\n"
        "open('c', 'wb')\n"
        "open('d', 'w', encoding='utf-8', newline='\\n')\n"
        "_textio.write_text(Path('e'), 'x')\n",
        encoding="utf-8",
    )
    assert _violations(src) == [
        "m.py:2: .write_text(",
        "m.py:3: open('w')",
    ]
