"""Every text file a bundled example reads or writes names its encoding.

gh-1368. Without ``encoding=``, Python on Windows uses the locale code page
(cp1252), not UTF-8. `full_workflow` read a generated `.pyi` that way and the
`—` in its class summary came back garbled, so an assertion about a correct
file failed -- on Windows only, which is the one platform no local run here
exercises. The examples are also what readers copy, so the fix belongs in
every call, not only the one that happened to meet a non-ASCII character.

Registration-free: every ``.py`` under ``examples/`` is walked, including the
``.steps/`` scripts the READMEs are assembled from.
"""

from __future__ import annotations

import ast
from pathlib import Path

EXAMPLES = Path(__file__).parent.parent / "src" / "just_makeit" / "examples"


def _modules() -> "list[Path]":
    out = []
    for p in sorted(EXAMPLES.rglob("*.py")):
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # a template with <<slots>>, not Python yet
            continue
        out.append(p)
    return out


def _violations(path: Path, base: Path = EXAMPLES) -> "list[str]":
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        if any(k.arg == "encoding" for k in n.keywords):
            continue
        f = n.func
        where = f"{path.relative_to(base)}:{n.lineno}"
        if isinstance(f, ast.Attribute) and f.attr == "read_text":
            if not n.args:
                out.append(f"{where}: read_text()")
        elif isinstance(f, ast.Attribute) and f.attr == "write_text":
            if len(n.args) < 2:
                out.append(f"{where}: write_text()")
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = n.args[1] if len(n.args) > 1 else None
            for k in n.keywords:
                if k.arg == "mode":
                    mode = k.value
            m = mode.value if isinstance(mode, ast.Constant) else "r"
            if isinstance(m, str) and "b" not in m:
                out.append(f"{where}: open({m!r})")
    return out


def test_the_walk_sees_the_examples():
    names = {p.relative_to(EXAMPLES).parts[0] for p in _modules()}
    assert {"full_workflow", "iqfile", "fir_filter"} <= names
    assert any(".steps" in p.parts for p in _modules())


def test_every_example_names_its_encoding():
    bad = [v for p in _modules() for v in _violations(p)]
    assert not bad, 'pass encoding="utf-8" (gh-1368):\n  ' + "\n  ".join(bad)


def test_the_check_catches_each_shape(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(
        "from pathlib import Path\n"
        "Path('a').read_text()\n"
        "Path('a').write_text('x')\n"
        "open('b')\n"
        "open('b', 'rb')\n"
        "Path('a').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    got = [v.split(": ", 1)[1] for v in _violations(src, tmp_path)]
    assert got == ["read_text()", "write_text()", "open('r')"]
