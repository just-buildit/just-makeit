"""A generated docstring reads back as its text: one writer spells it.

jm writes docstrings into the ``.pyi`` stubs and Python files it generates,
around text it was handed -- a manifest ``doc``, a header ``@brief``, a
name. Each layout spelled its own ``\"\"\"`` around that text, and they
disagreed about what the text could hold: a triple quote ended the literal
early (the stub did not parse, gh-2606), a backslash was read as an escape
(``\\n`` became a newline, ``\\l`` a SyntaxWarning), and only
``render_numpy_doc`` knew to go raw (gh-652).

Now `_docstring.docstring_literal` is the one place a docstring literal is
spelled, and its value is exactly the text it is given.

GATES:

1. Registration-free over jm's source: no string literal in
   ``src/just_makeit`` (bundled ``examples/`` are user code, not jm's)
   spells a triple quote, outside `docstring_literal` and the
   ``TRIPLE_QUOTE`` constant a reader compares against. A literal that is
   not a Python docstring at all says so on its line, ``# not a
   docstring: <reason>``, and the reason must be given. ``TRIPLE_QUOTE``
   is never built into text (an f-string, ``+``, ``%``). The scan reads
   the AST, so a formatter's line wrapping cannot hide a site, and the
   gate proves it is armed on a planted writer of each shape.
2. No template spells a slot inside a docstring: the text such a slot
   carries would reach the literal unescaped. A template's docstring is a
   slot of its own, rendered by the writer.
3. Round trip: for every writer, text that would end the literal or be
   read as an escape gives a stub that parses with every warning an error,
   and the docstring reads back as the text.
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from just_makeit import _docstring as D
from just_makeit import _render as R

SRC = Path(D.__file__).parent
TEMPLATES = SRC / "templates"

#: The one writer, and the one constant.
_WRITER = ("_docstring.py", "docstring_literal")
_CONSTANT = "TRIPLE_QUOTE"
_MARK = re.compile(r"#\s*not a docstring:\s*(\S.*)$")
_Q = '"' * 3


def _modules() -> list[Path]:
    # `examples/` is user code; `templates/` is not Python until rendered,
    # and gate 2 reads it.
    return sorted(
        p
        for p in SRC.rglob("*.py")
        if not {"examples", "templates"} & set(p.relative_to(SRC).parts)
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of the docstring constants of jm's own modules, classes and
    functions: they document jm, they are not written anywhere."""
    out: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(
            n,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            body = n.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _inside(node: ast.AST, parents: dict, name: str) -> bool:
    """Whether *node* is in the function *name*, or the assignment to it."""
    while id(node) in parents:
        node = parents[id(node)]
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return True
    return False


def _marked(lines: list[str], node: ast.AST) -> bool:
    end = getattr(node, "end_lineno", node.lineno)
    return any(_MARK.search(lines[i - 1]) for i in range(node.lineno, end + 1))


def _names_constant(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == _CONSTANT) or (
        isinstance(node, ast.Attribute) and node.attr == _CONSTANT
    )


def violations(path: Path, text: str) -> list[str]:
    """Every site in *text* (the module at *path*) spelling a docstring
    literal around the one writer."""
    tree = ast.parse(text)
    lines = text.splitlines()
    docs = _docstring_nodes(tree)
    parents = _parents(tree)
    out: list[str] = []
    for n in ast.walk(tree):
        where = f"{path.name}:{getattr(n, 'lineno', '?')}"
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and _Q in n.value
            and id(n) not in docs
        ):
            if path.name == _WRITER[0] and (
                _inside(n, parents, _WRITER[1])
                or _inside(n, parents, _CONSTANT)
            ):
                continue
            if _marked(lines, n):
                continue
            out.append(f"{where}: a triple quote spelled outside the writer")
        if isinstance(n, ast.JoinedStr) and any(
            isinstance(v, ast.FormattedValue) and _names_constant(v.value)
            for v in n.values
        ):
            out.append(f"{where}: {_CONSTANT} built into an f-string")
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
            if _names_constant(n.left) or _names_constant(n.right):
                out.append(f"{where}: {_CONSTANT} built into text")
    return out


def test_one_writer_spells_a_docstring_literal():
    found = [v for p in _modules() for v in violations(p, p.read_text())]
    assert not found, "\n".join(found)


def test_every_exemption_gives_a_reason():
    for p in _modules():
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if "not a docstring" in line and "#" in line:
                m = _MARK.search(line)
                assert m and m.group(1).strip(), f"{p.name}:{i}"


@pytest.mark.parametrize(
    "planted",
    [
        'x = \'"""\' + doc',
        'x = f\'    """{doc}"""\'',
        'x = (\n    f\'"""{doc}\'\n    \'"""\'\n)',
        "x = f'{TRIPLE_QUOTE}{doc}'",
        "x = TRIPLE_QUOTE + doc",
        "x = D.TRIPLE_QUOTE + doc",
        "x = '%s' % TRIPLE_QUOTE",
    ],
    ids=[
        "concat",
        "fstring",
        "wrapped",
        "const-f",
        "const-add",
        "attr",
        "mod",
    ],
)
def test_the_scan_is_armed(planted):
    assert violations(Path("_planted.py"), planted)


@pytest.mark.parametrize(
    "allowed",
    [
        'def f():\n    """Doc with \\"\\"\\"quotes\\"\\"\\"."""',
        "ok = line.startswith(TRIPLE_QUOTE)",
        "ok = TRIPLE_QUOTE in body",
        'x = \'"""\'  # not a docstring: a TOML heredoc',
        'x = re.compile(r\'\\"\\"\\"(.*)\')',
    ],
    ids=["own-doc", "startswith", "in", "marked", "escaped-regex"],
)
def test_the_scan_lets_through_what_it_allows(allowed):
    assert not violations(Path("_planted.py"), allowed)


def test_the_scan_reaches_the_writer():
    text = (SRC / _WRITER[0]).read_text()
    assert f"def {_WRITER[1]}(" in text
    assert f"{_CONSTANT} = " in text


# -- 2. templates ------------------------------------------------------------


def _template_doc_slots(text: str) -> list[str]:
    """The slots inside a template's docstrings, as written in *text*."""
    return [
        s
        for m in re.finditer(r'"""(.*?)"""', text, re.S)
        for s in re.findall(r"<<\w+>>", m.group(1))
    ]


def _python_templates() -> list[Path]:
    return [
        p for p in sorted(TEMPLATES.rglob("*")) if p.suffix in (".py", ".pyi")
    ]


def test_no_template_pastes_a_slot_into_a_docstring():
    """A slot in a template's module docstring is rendered by the writer
    (`_render.PyTemplate`); a slot in any other docstring is pasted between
    quotes it could close, and is refused."""
    found = {}
    for p in _python_templates():
        rel = str(p.relative_to(TEMPLATES))
        loaded = R._load(rel)
        left = _template_doc_slots(str(loaded))
        if left:
            found[rel] = left
        if isinstance(loaded, R.PyTemplate):
            assert "<<jm_module_doc>>" in loaded, rel
    assert not found, found
    # Armed: the templates this exists for do carry a slot in a docstring.
    assert any(_template_doc_slots(p.read_text()) for p in _python_templates())


def test_the_template_scan_is_armed():
    assert _template_doc_slots('"""Bench for <<Component>>."""\n')
    assert _template_doc_slots('x = 1\n\ndef f():\n    """<<doc>>"""\n')


@pytest.mark.parametrize(
    "rel",
    [
        str(p.relative_to(TEMPLATES))
        for p in _python_templates()
        if isinstance(R._load(str(p.relative_to(TEMPLATES))), R.PyTemplate)
    ],
)
def test_a_template_docstring_reads_back_with_any_slot_value(rel):
    tmpl = R._load(rel)
    hostile = 'q"""b\\n\\'
    slots = set(re.findall(r"<<(\w+)>>", tmpl.doc))
    ctx = {k: hostile for k in slots}
    want = R.render(tmpl.doc, ctx)
    got = R.render(tmpl, ctx)
    # The rest of the file has slots this context leaves empty, so it is
    # not Python yet; the docstring is, and it is the writer's literal.
    lit = D.docstring_literal(want)
    assert got.count(lit) == 1, rel
    assert _reads_back(lit) == want


# -- 3. round trip -----------------------------------------------------------

#: Text that would end a literal early, or be read as an escape.
UNSAFE = [
    'Say """hi""" to it.',
    'Ends in a "quote"',
    "Ends in a backslash \\",
    "A \\n is not a newline.",
    "Gain is 20\\log_{10}(g).",
    "Path C:\\tmp\\new.",
    'Both \\n and """ at once \\',
]


def _reads_back(literal: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        tree = ast.parse("x = " + literal + "\n")
    return ast.literal_eval(tree.body[0].value)


@pytest.mark.parametrize("text", UNSAFE)
def test_the_writer_round_trips(text):
    assert _reads_back(D.docstring_literal(text)) == text
    assert _reads_back(D.docstring_literal(text + "\n    ")) == text + "\n    "


def _layout_value(lines: list[str]) -> str:
    src = "def f():\n" + "\n".join(lines) + "\n"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        tree = ast.parse(src)
    return ast.get_docstring(tree.body[0], clean=False) or ""


@pytest.mark.parametrize("text", UNSAFE)
def test_each_layout_round_trips(text):
    long = text + " " + "word " * 20
    assert _layout_value(D.summary_docstring(text, indent=4)) == text
    assert " ".join(
        _layout_value(D.summary_docstring(long, indent=4)).split()
    ) == " ".join(long.split())
    assert _layout_value(D.authored_docstring([text], 4)) == text
    two = _layout_value(D.authored_docstring([text, "", text], 4))
    assert two.split("\n")[0] == text and two.split("\n")[2] == "    " + text
    cls = _layout_value(
        D.class_docstring(text, params=[D.ClassParam("x : int", (text,))])
    )
    assert cls.startswith(text)
    num = _layout_value(
        D.render_numpy_doc(
            None, "f", [], "None", text, indent=4, authored_doc=text
        )
    )
    assert num.startswith(text)


@pytest.mark.parametrize("text", UNSAFE)
def test_a_module_docstring_round_trips(text):
    from just_makeit._context._modpath import _py_docstring

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        tree = ast.parse(_py_docstring(text))
    assert ast.get_docstring(tree, clean=False) == text


@pytest.mark.parametrize("text", UNSAFE)
def test_the_reflow_keeps_what_a_docstring_reads(text):
    """`_pyfmt` rewraps a one-line docstring read off a stub: its escapes
    must be neither doubled nor dropped."""
    from just_makeit._pyfmt import reflow_oneline_docstring

    long = text + " " + "word " * 20 + "end."
    line = "        " + D.docstring_literal(long)
    block = reflow_oneline_docstring(line, 79)
    got = _layout_value(block)
    assert " ".join(got.split()) == " ".join(long.split())
