"""gh-2459 (part 1 of 2): every C source directory jm writes or reads is
spelled by `_incpath`.

gh-2427 moved a component's header, test and bench under its module's path
in the ``nested`` layout; its C stays in ``native/src/<name>/``. Moving that
too means one function decides where a component's C is, so part 1 routes
every ``native/src/<name>`` path through `_incpath.src_dir` / `src_path` /
`src_rel` (and the root wiring through `subdir_line` / `SUBDIR_RE`), with
no change to what any layout writes.

GATES:

1. Registration-free: no jm module but `_incpath` spells a source directory
   from a name -- ``f"native/src/{x}"``, ``"native/src/" + x``, ``%`` and
   ``.format`` strings, ``"/".join``, ``root / "native" / "src" / x``,
   ``Path("native", "src", x)``, ``.joinpath``, ``os.path.join``, the same
   through a name bound to ``root / "native"`` first (plain or annotated),
   ``INC.src_root(root) / x`` and ``f"{INC.SRC_DIR}/{x}"`` -- and no
   template spells ``native/src/<<component>>``. Prose is excluded.
2. The gate sees every shape it guards, and lets through what it allows: a
   scan of the whole tree, a fixed file below it, a message naming the
   directory literally.
3. The answers themselves: `src_dir` and its peers agree with each other
   and with the root wiring's regex, in every layout.

Byte-identity of every layout's output with the release before is shown by
a ``diff -r`` of whole scaffolds and every bundled example (the PR body);
the existing suites, which pin the flat tree, are the standing check.
"""

from __future__ import annotations

import ast
import re

import pytest

from just_makeit import _incpath as INC
from test_gh2427_nested_layout import (
    PKG,
    _incpath_call,
    _incpath_names,
    _modules,
    _prose,
    _shape,
)

#: The constants a string may name in place of the literal.
_KNOWN = {"SRC_DIR": INC.SRC_DIR, "APP_DIR": INC.APP_DIR}

#: The source tree's path parts, read off the owner.
_PARTS = INC.SRC_DIR.split("/")

#: A source directory spelled from a name: the tree, then a name (NUL), a
#: regex group, a format field or a template slot.
_HAND = re.compile(re.escape(INC.SRC_DIR) + r"/(?:\x00|[(\[{]|<<)")

#: A template's source directory from a slot: ``native/src/<<component>>/``.
#: The ``<<project_underscore>>_lib.c`` stub is a file at the tree's root.
_TEMPLATE_HAND = re.compile(re.escape(INC.SRC_DIR) + r"/<<\w+>>/")

#: The `_incpath` calls returning the source tree itself.
_TREE_CALLS = {"src_root"}

#: Calls that join their arguments as path parts.
_JOINERS = {"Path", "PurePath", "PurePosixPath", "PureWindowsPath"}

_NAME = object()


def _tokens(node, names, bound, known=None) -> "list | None":
    """*node* as a path: a list of literal parts, with :data:`_NAME` for
    each part built from anything else. ``None`` when *node* is not a path
    join, a literal, the source tree, or a name bound to one of those.
    *known* maps the names a string may stand for to their text."""
    known = _KNOWN if known is None else known
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _tokens(node.left, names, bound, known)
        right = _tokens(node.right, names, bound, known)
        return (left if left is not None else [_NAME]) + (
            right if right is not None else [_NAME]
        )
    if isinstance(node, ast.Call):
        f = node.func
        args = None
        base: "list" = []
        if isinstance(f, ast.Attribute) and f.attr == "joinpath":
            got = _tokens(f.value, names, bound, known)
            base = got if got is not None else [_NAME]
            args = node.args
        elif (
            isinstance(f, ast.Attribute)
            and f.attr == "join"
            and isinstance(f.value, ast.Attribute)
            and f.value.attr == "path"
        ):
            args = node.args  # os.path.join(...)
        elif (isinstance(f, ast.Name) and f.id in _JOINERS) or (
            isinstance(f, ast.Attribute) and f.attr in _JOINERS
        ):
            args = node.args
        elif _incpath_call(node, names) in _TREE_CALLS:
            return list(_PARTS)
        if args is not None:
            out = list(base)
            for a in args:
                got = _tokens(a, names, bound, known)
                out += got if got is not None else [_NAME]
            return out
        return None
    if isinstance(node, ast.NamedExpr):
        return _tokens(node.value, names, bound, known)
    if isinstance(node, ast.Name) and node.id in bound:
        return list(bound[node.id])
    text = _shape(node, known)
    if text is None or "\x00" in text:
        return None
    return [p for p in text.split("/") if p]


def _hand_join(tokens: list) -> bool:
    """Whether *tokens* reach a name below the source tree."""
    n = len(_PARTS)
    for i in range(len(tokens) - n + 1):
        if tokens[i : i + n] == _PARTS and _NAME in tokens[i + n :]:
            return True
    return False


def _tail(tokens: "list | None") -> "list | None":
    """What a name bound to *tokens* carries into a later join: the
    source tree's leading parts when *tokens* ends partway into it."""
    if not tokens:
        return None
    for k in range(len(_PARTS), 0, -1):
        if tokens[-k:] == _PARTS[:k]:
            return [_NAME, *_PARTS[:k]]
    return None


def _findings(tree, rel) -> "list[str]":
    prose = _prose(tree)
    names = _incpath_names(tree)
    # Each node's enclosing function (or the module): a name means what its
    # own scope bound it to.
    scope_of: "dict[int, int]" = {}

    def mark(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            scope_of[id(child)] = id(inner)
            mark(child, inner)

    mark(tree, tree)
    # Per scope: names bound to a path ending partway into the source tree,
    # and names bound to a string (a local ``S = "native/src"``). A function
    # also sees what the module bound.
    bound: "dict[int, dict[str, list]]" = {}
    strings: "dict[int, dict[str, str]]" = {}
    for n in ast.walk(tree):
        target = value = None
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            target, value = n.targets[0], n.value
        elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)):
            target, value = n.target, n.value
        if isinstance(target, ast.Name) and value is not None:
            scope = scope_of.get(id(n), id(tree))
            tail = _tail(_tokens(value, names, {}))
            if tail is not None:
                bound.setdefault(scope, {})[target.id] = tail
            text = _shape(value, _KNOWN)
            if text is not None and "\x00" not in text:
                strings.setdefault(scope, {})[target.id] = text

    def visible(table, node):
        scope = scope_of.get(id(node), id(tree))
        return {**table.get(id(tree), {}), **table.get(scope, {})}

    hits: "list[str]" = []
    seen: "set[int]" = set()

    def check_shape(node, known) -> None:
        text = _shape(node, known)
        if text is not None and _HAND.search(text):
            hits.append(f"{rel}:{node.lineno}  {text[:70]!r}")

    for node in ast.walk(tree):
        if id(node) in seen or id(node) in prose:
            continue
        here = visible(bound, node)
        known = {**_KNOWN, **visible(strings, node)}
        is_join = (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        ) or (
            isinstance(node, ast.Call)
            and _incpath_call(node, names) not in _TREE_CALLS
            and _tokens(node, names, here, known) is not None
        )
        if is_join:
            got = _tokens(node, names, here, known)
            if got is not None and _hand_join(got):
                hits.append(f"{rel}:{node.lineno}  <src> / name")
            # A join's own parts may each be a string built from a name:
            # ``root / f"native/src/{x}"``, ``Path("native/src/" + x)``.
            for sub in ast.walk(node):
                seen.add(id(sub))
                if sub is not node and id(sub) not in prose:
                    check_shape(sub, known)
            continue
        check_shape(node, known)
    return sorted(set(hits))


def _template_findings() -> "list[str]":
    hits = []
    for path in sorted((PKG / "templates").rglob("*")):
        if not path.is_file():
            continue
        for n, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if _TEMPLATE_HAND.search(line):
                hits.append(f"{path.relative_to(PKG)}:{n}  {line.strip()}")
    return hits


def test_only_incpath_spells_a_source_directory():
    hits = []
    for rel, tree in _modules():
        hits += _findings(tree, rel)
    hits += _template_findings()
    assert hits == [], (
        "spell a component's C source directory through `_incpath` "
        "(INC.src_dir, INC.src_path, INC.src_rel, INC.subdir_line, the "
        "<<src_dir>> slot):\n" + "\n".join(hits)
    )


#: Every shape the gate refuses, one statement per line.
_PROBE = """\
a = f"native/src/{comp}/{comp}_core.c"
b = "native/src/" + comp
c = "native/src/%s" % comp
d = "native/src/{}".format(comp)
e = "/".join(["native", "src", comp])
f = root / "native" / "src" / comp
g = Path("native") / "src" / comp
h = Path("native", "src", comp)
i = root.joinpath("native", "src", comp)
j = os.path.join(root, "native", "src", comp)
k = INC.src_root(root) / comp
l = f"{INC.SRC_DIR}/{comp}"
m = root / "native/src" / comp
n = re.compile(r"add_subdirectory\\(native/src/(\\w+)\\)")
o = INC.SRC_DIR + "/" + comp
def two_step(root, comp):
    native = root / "native"
    return native / "src" / comp
def annotated(root, comp):
    tree: Path = root / "native" / "src"
    return tree / comp
def walrus(root, comp):
    return (t := INC.src_root(root)).joinpath(comp)
p = root / f"native/src/{comp}/x.c"
q = Path(f"native/src/{comp}")
r = Path("native/src/" + comp)
s = Path(root, f"native/src/{comp}")
t = os.path.join(root, f"native/src/{comp}")
u = root / ("native/src/%s" % comp)
v = root / "native/src/{}".format(comp)
w = pathlib.Path("native", "src", comp)
def local_constant(comp):
    S = "native/src"
    return f"{S}/{comp}", S + "/" + comp
x = os.sep.join(["native", "src", comp])
y = "native" + os.sep + "src" + os.sep + comp
"""

#: ...and what it lets through: a scan of the tree, a fixed file below it,
#: the header tree, a message naming the directory literally.
_ALLOWED = """\
a = INC.src_root(root).glob("*/CMakeLists.txt")
b = INC.src_root(root)
c = root / "native" / "src" / "app"
d = (root / "native").rglob("*.c")
e = INC.src_path(root, comp, cfg) / f"{comp}_core.c"
f = f"{INC.SRC_DIR}/*/*_core.c"
g = "  C sources:  native/src/ not found"
h = INC.src_rel(comp, "CMakeLists.txt", cfg)
"""


def test_the_gate_sees_what_it_guards():
    """Each refused shape is a finding on its own line, and each allowed
    one is none."""
    found = _findings(ast.parse(_PROBE), "probe")
    lines = {f.split()[0] for f in found}
    want = {
        f"probe:{n}"
        for n in (*range(1, 16), 18, 21, *range(23, 32), 34, 35, 36)
    }
    assert lines == want, sorted(lines ^ want)
    assert _findings(ast.parse(_ALLOWED), "probe") == []
    assert _TEMPLATE_HAND.search("<<src_dir>>/<<component>>_core.c") is None
    assert (
        _TEMPLATE_HAND.search("native/src/<<project_underscore>>_lib.c")
        is None
    )
    assert _TEMPLATE_HAND.search(
        "native/src/<<component>>/<<component>>_core.c"
    )
    assert len(list(_modules())) > 50
    uses = sum(
        p.read_text(encoding="utf-8").count("INC.src_")
        for p in PKG.glob("*.py")
    )
    assert uses >= 60, uses


@pytest.mark.parametrize("layout", INC.LAYOUTS)
def test_the_answers_agree(layout):
    """`src_dir`, `src_rel`, `subdir_line` and `SUBDIR_RE` name one
    directory per component, in every layout."""
    cfg = {
        "project": {"name": "p", "layout": layout},
        "module": {"rx.ss": {"objects": ["fir"]}},
        "fir": {},
        "amp": {},
    }
    for name in ("fir", "amp", "rx_ss"):
        d = INC.src_dir(name, cfg)
        assert INC.src_rel(name, "x.c", cfg) == f"{d}/x.c"
        line = INC.subdir_line(name, cfg)
        m = re.fullmatch(INC.SUBDIR_RE + r"\n", line)
        assert m and m.group(1) == name, line
        assert d.startswith(INC.SRC_DIR + "/")
    with pytest.raises(TypeError):
        INC.src_dir("fir", "p")
