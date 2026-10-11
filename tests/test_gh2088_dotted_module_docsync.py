"""gh-2088: a module's files are named for its cname, never its id.

`C.module_paths(id)` splits a module name into its roles (gh-983): the
dotted id (`dsp.filt`) is a manifest KEY and names nothing on disk; the
cname (`dsp_filt`) is the native directory and every file stem in it, and
the fragment file `modules/dsp_filt.toml`.

`_docsync.refresh_module_fragment_docs` built its path from the id:
`native/src/dsp.filt/dsp.filt_ext_o.c`. That file never exists, so every
fragment was skipped as missing, and `jm apply` refreshed no runtime doc in
a dotted module -- neither a manifest `doc` edit nor a header Doxygen edit
-- and said nothing. A reader of a wrong path is the quiet member of this
family: gh-983's "no generated path contains the id" invariant only sees
paths that are WRITTEN, and this one was only ever read.

So the gate has two halves:

- the behaviour: on a dotted module, a doc edit reaches the fragment after
  `apply` (with the flat module as the control); and
- the class: a source scan refusing a `native/src/<X>` or `modules/<X>`
  path whose `<X>` is a raw module id, read from the AST of every jm
  module, so a new site is covered the moment it is written.

GATE: no path under native/src/ or modules/ is built from a raw module id.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _jmrun import run_cli

PKG = Path(__file__).parent.parent / "src" / "just_makeit"

DOTTED, CNAME = "dsp.filt", "dsp_filt"


# ── The behaviour ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module, cname",
    [(DOTTED, CNAME), ("filt", "filt")],
    ids=["dotted", "flat"],
)
def test_a_doc_edit_reaches_the_fragment_after_apply(
    tmp_path: Path, module: str, cname: str
):
    """Both doc sources a fragment's runtime `__doc__` reads: the manifest
    `doc` of a method, and the header Doxygen of `step`. The flat module is
    the control -- it always worked, so a red dotted case is about the path,
    not about the doc sync."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for argv in (
        ("module", module),
        ("object", "o", "--module", module),
        ("method", "o", "m", "--module", module, "--doc", "Old doc."),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, r.stderr

    frag_toml = root / "objects" / "o.toml"
    text = frag_toml.read_text(encoding="utf-8")
    assert 'doc = "Old doc."' in text, text
    frag_toml.write_text(
        text.replace('doc = "Old doc."', 'doc = "New doc."'), encoding="utf-8"
    )
    header = root / "native" / "inc" / "p" / "o" / "o_core.h"
    body = header.read_text(encoding="utf-8")
    assert "@brief Process one input sample." in body, body
    header.write_text(
        body.replace(
            "@brief Process one input sample.", "@brief Edited step doc."
        ),
        encoding="utf-8",
    )

    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stderr
    fragment = root / "native" / "src" / cname / f"{cname}_ext_o.c"
    got = fragment.read_text(encoding="utf-8")
    assert '"New doc.\\n"' in got, got
    assert '"Edited step doc.\\n"' in got, got
    assert "Old doc." not in got


def test_a_module_conflict_names_the_fragment_file_that_exists(
    tmp_path: Path,
):
    """The refusal for a module key set in two places points at the file
    that holds it: `modules/dsp_filt.toml`, not `modules/dsp.filt.toml`."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    assert run_cli("module", DOTTED, cwd=root).returncode == 0
    assert (root / "modules" / f"{CNAME}.toml").is_file()
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f'\n[module."{DOTTED}"]\nobjects = ["elsewhere"]\n',
        encoding="utf-8",
    )
    r = run_cli("status", cwd=root)
    assert r.returncode != 0
    assert f"(modules/{CNAME}.toml)" in r.stderr, r.stderr


# ── The class ────────────────────────────────────────────────────────────────

# Names that hold a module id by jm's own convention: every `_config`
# helper takes the id as `module`, and the loops over it say `mod`.
_ID_NAMES = frozenset({"mod", "module", "module_id", "mod_id"})
# Calls that return module ids, for a name bound from one.
_ID_SOURCES = frozenset(
    {"modules", "module_of", "component_module", "resolve_module"}
)
# The directories a module owns a file in, by its cname.
_DIRS = (("native", "src"), ("modules",))


def _callee(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _yields_ids(node: ast.AST) -> bool:
    """`C.modules(cfg)`, and the same through `sorted(...)` or `list(...)`."""
    if _callee(node) in _ID_SOURCES:
        return True
    return (
        _callee(node) in {"sorted", "list", "set", "tuple", "reversed"}
        and bool(node.args)  # type: ignore[attr-defined]
        and _yields_ids(node.args[0])  # type: ignore[attr-defined]
    )


def _scope_ids(fn: ast.AST, outer: frozenset) -> frozenset:
    """The names in *fn* that hold a raw module id.

    A name rebound to anything else (`module = mp.cname`, as
    `_render.render_module_ext_aggregator` does) is no longer one.
    """
    args = fn.args  # type: ignore[attr-defined]
    ids = set(outer) | {
        a.arg
        for a in args.posonlyargs + args.args + args.kwonlyargs
        if a.arg in _ID_NAMES
    }
    rebound: set = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.For, ast.comprehension)):
            if isinstance(n.target, ast.Name) and _yields_ids(n.iter):
                ids.add(n.target.id)
            ids |= {
                t.id
                for t in ast.walk(n.target)
                if isinstance(t, ast.Name) and t.id in _ID_NAMES
            }
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    (ids if _yields_ids(n.value) else rebound).add(t.id)
    return frozenset(ids - rebound)


def _lit(node: ast.AST) -> str | None:
    """A string literal, or the one in `Path("native")`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _callee(node) == "Path" and len(node.args) == 1:  # type: ignore
        return _lit(node.args[0])  # type: ignore[attr-defined]
    return None


def _lead_name(node: ast.AST) -> str | None:
    """`mod`, or the `mod` an f-string such as `f"{mod}.toml"` starts with."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.FormattedValue) and isinstance(
            first.value, ast.Name
        ):
            return first.value.id
    return None


def _chain(node: ast.AST) -> list:
    """`root / "native" / "src" / mod` as its operands, left to right."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _chain(node.left) + [node.right]
    return [node]


def raw_id_paths(source: str) -> list[tuple[int, str]]:
    """Every `(line, name)` where *source* builds a module-owned path from a
    raw module id: a `/` join (`root / "native" / "src" / mod`) or an
    f-string (`f"native/src/{mod}/..."`, `f"modules/{mod}.toml"`)."""
    out: list[tuple[int, str]] = []

    def visit(node: ast.AST, ids: frozenset) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, _scope_ids(child, ids))
                continue
            inner = (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and node.left is child
            )
            if (
                isinstance(child, ast.BinOp)
                and isinstance(child.op, ast.Div)
                and not inner
            ):
                ops = _chain(child)
                lits = [_lit(o) for o in ops]
                for i in range(len(ops)):
                    for d in _DIRS:
                        j = i + len(d)
                        if j < len(ops) and tuple(lits[i:j]) == d:
                            name = _lead_name(ops[j])
                            if name in ids:
                                out.append((child.lineno, name))
            if isinstance(child, ast.JoinedStr):
                vals = child.values
                for c, v in zip(vals, vals[1:]):
                    if (
                        isinstance(c, ast.Constant)
                        and isinstance(v, ast.FormattedValue)
                        and isinstance(v.value, ast.Name)
                        and v.value.id in ids
                        and any(
                            str(c.value).endswith("/".join(d) + "/")
                            for d in _DIRS
                        )
                    ):
                        out.append((child.lineno, v.value.id))
            visit(child, ids)

    visit(ast.parse(source), frozenset())
    return out


def _jm_sources() -> list[Path]:
    # `templates/` holds `.py` files with `<<slot>>` placeholders: they are
    # inputs to a render, not jm's code, and do not parse.
    return sorted(p for p in PKG.rglob("*.py") if "templates" not in p.parts)


def test_no_module_path_is_built_from_a_raw_module_id():
    found = [
        f"{p.relative_to(PKG).as_posix()}:{line}: {name}"
        for p in _jm_sources()
        for line, name in raw_id_paths(p.read_text(encoding="utf-8"))
    ]
    assert found == [], (
        "a module's native dir and fragment file are named for its cname; "
        "read `C.module_paths(<id>).cname` (gh-983, gh-2088):\n"
        + "\n".join(found)
    )


@pytest.mark.parametrize(
    "snippet, flagged",
    [
        ('def f(root, mod):\n    return root / "native" / "src" / mod\n', 1),
        ("def f(mod):\n    return f'native/src/{mod}/x.c'\n", 1),
        (
            "def f(cfg, root):\n"
            "    for m in sorted(C.modules(cfg)):\n"
            '        root / "modules" / f"{m}.toml"\n',
            1,
        ),
        (
            "def f(root, mod):\n"
            "    cname = C.module_paths(mod).cname\n"
            '    return root / "native" / "src" / cname\n',
            0,
        ),
        (
            "def f(root, module):\n"
            "    module = C.module_paths(module).cname\n"
            '    return root / "native" / "src" / module\n',
            0,
        ),
        ('def f(root, comp):\n    return root / "native" / "src" / comp\n', 0),
    ],
    ids=["join", "fstring", "loop", "cname", "rebound", "component"],
)
def test_the_scan_is_armed(snippet: str, flagged: int):
    """The scan finding nothing on jm must mean jm is clean, not that the
    scan cannot see: each spelling the bug took is flagged, each correct
    one is not."""
    assert len(raw_id_paths(snippet)) == flagged
