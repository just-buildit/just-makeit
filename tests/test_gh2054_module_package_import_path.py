"""gh-2054: a module declaring `package` is imported from its PACKAGE.

An object in a module whose ``[module.X] package`` is set (gh-523) lands in
that package, and the package's ``__init__.py`` re-exports it: with
``package = "other"`` the class is ``p.other.O``. But jm named the module
id's path wherever it named the class -- the `.pyi` and runtime ``__doc__``
doctests said ``from p.mod import O``, the gh-1404 element contract was
written to ``src/p/mod/tests/`` and imported from there, the ``.pyi`` import
of an ``object`` reference said ``from .mod import O``, and the pep723 and
console apps did the same. ``jm status --check`` exited 0 on that tree: it
compares the tree to jm's own render, which reads the same wrong path.

There were two spellings of "where a module object's class lives".
``_invariants.file_for`` read ``C.module_paths(module).pypath``, and every
caller of ``_docstring.class_import_line`` passed the module id; neither
read ``C.module_package``. Now ``C.module_package_resolved`` is the one
answer, every import jm writes reads it, and the contract's directory is
the one ``test_<obj>.py`` is written to.

GATE: a module with ``package`` set, carrying a pair, a view, a blockwise
      object, a capsule other components reference, and both apps.

      - every call site of the import-path joiner
        (``_docstring.class_import_path`` / ``class_import_line``, found by
        reading jm's source -- no list here) ran while the fixture was
        built, so the two checks below cannot be vacuous for any of them;
      - every path the joiner returned holds the class, and every
        ``from <path> import <Class>`` in the generated tree names one.
        "Holds" is read off the generated ``__init__.py`` re-exports, which
        a different writer produces -- an independent oracle, not jm's
        render of itself;
      - the contract sits beside the module's ``test_<obj>.py``.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import re
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh1978_remove_leaves_tree_in_sync import _reader, _writer

import just_makeit
from just_makeit import _config as C
from just_makeit import _docstring

_M = ("--module", "mod")

#: The joiner every generated import path is spelled by (gh-1208).
_JOINERS = ("class_import_path", "class_import_line")
_SRC = Path(just_makeit.__file__).resolve().parent
_DOCSTRING = Path(_docstring.__file__).resolve()
_HERE = Path(__file__).resolve()


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)


def _edit(root: Path, change) -> None:
    """Change the manifest the way an author does: keys no CLI flag writes."""
    cfg = C.load(root)
    change(cfg)
    C.save(root, cfg)


def _rebind(old, new) -> None:
    """Point every name in jm bound to *old* at *new*, aliases included.

    By identity, not by name: `_invariants` imports the joiner under an
    alias, and a module imported while the spy is in place binds the spy,
    so restoring by the same walk is what leaves no spy behind.
    """
    for mod in list(sys.modules.values()):
        if not getattr(mod, "__name__", "").startswith("just_makeit"):
            continue
        for attr, val in list(vars(mod).items()):
            if val is old:
                setattr(mod, attr, new)


@contextlib.contextmanager
def _spy(calls: list):
    """Record every joiner call: (caller file, caller line, args, result)."""
    real = {n: getattr(_docstring, n) for n in _JOINERS}

    def wrap(fn):
        sig = inspect.signature(fn)

        def spy(*args, **kwargs):
            out = fn(*args, **kwargs)
            f = sys._getframe(1)
            # Past the joiner itself (class_import_line calls the path) and
            # this wrapper: the frame left is the site that asked.
            while Path(f.f_code.co_filename).resolve() in (_DOCSTRING, _HERE):
                f = f.f_back
            got = sig.bind(*args, **kwargs).arguments
            calls.append(
                (Path(f.f_code.co_filename).resolve(), f.f_lineno, got, out)
            )
            return out

        return spy

    spies = {n: wrap(fn) for n, fn in real.items()}
    for n in _JOINERS:
        _rebind(real[n], spies[n])
    try:
        yield
    finally:
        for n in _JOINERS:
            _rebind(spies[n], real[n])


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """The issue's tree, widened until every joiner site runs on it."""
    tmp = tmp_path_factory.mktemp("gh2054")
    calls: list = []
    with _spy(calls):
        _ok(tmp, "new", "p")
        root = tmp / "p"
        _ok(root, "module", "mod")
        _edit(root, lambda cfg: cfg["module"]["mod"].update(package="other"))
        for argv in (*_writer("o", *_M), _reader("o", *_M)):
            _ok(root, *argv)
        # Array in, array out: `make_step_ctx`'s blockwise doctest.
        _ok(root, "object", "b", "--preset", "blockwise", *_M)
        _ok(root, "view", "o", "Peek", *_M, "--create-fn", "o_create_peek",
            "--doc", "A peek.")  # fmt: skip
        # A standalone object, and one naming O through an `object`
        # reference, which needs O to publish a capsule.
        _ok(root, "object", "s")
        _ok(root, "property", "o", "_capsule", "--type", "capsule",
            "--capsule", "p.o", *_M)  # fmt: skip
        _ok(root, "object", "user")

        def _declare(cfg: dict) -> None:
            # An authored class doc is what renders the runtime class block.
            cfg["o"]["doc"] = "An o."
            cfg["s"]["doc"] = "An s."
            cfg["user"]["init_params"] = [
                {"name": "src", "object": "o.O", "required": True}
            ]

        _edit(root, _declare)
        _ok(root, "apply")
        _ok(root, "app", "--object", "o", *_M, "--target", "pep723")
        _ok(root, "app", "--object", "o", *_M, "--target", "console",
            "--name", "o_cli")  # fmt: skip
    return root, calls


def _sites() -> "list[tuple[Path, int, int]]":
    """Every call of the joiner in jm's source, as (file, first, last line).

    Read from the source so a new face is checked the day it is written.
    Aliased imports are followed; `_docstring` itself is the joiner, and
    ``templates/`` holds render input, not code jm runs.
    """
    out = []
    for py in sorted(_SRC.rglob("*.py")):
        if (
            py.resolve() == _DOCSTRING
            or "templates" in py.relative_to(_SRC).parts[:1]
        ):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names = set(_JOINERS)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).endswith("_docstring"):
                names |= {
                    a.asname
                    for a in node.names
                    if a.name in _JOINERS and a.asname
                }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in names:
                out.append((py.resolve(), node.lineno, node.end_lineno))
    return out


def _exports(root: Path) -> "dict[str, set[str]]":
    """Dotted module -> the names it provides, from the re-exports.

    ``src/p/other/__init__.py`` saying ``from .mod import O`` makes ``O``
    importable from ``p.other`` and from ``p.other.mod``. That file is
    written by the module's own re-export writer, not by any face this
    test checks.
    """
    out: "dict[str, set[str]]" = {}
    src = root / "src"
    for init in sorted(src.rglob("__init__.py")):
        here = ".".join(init.parent.relative_to(src).parts)
        tree = ast.parse(init.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                got = {a.asname or a.name for a in node.names}
                out.setdefault(here, set()).update(got)
                if node.module:
                    out.setdefault(f"{here}.{node.module}", set()).update(got)
    return out


def _classes(root: Path) -> "set[str]":
    cfg = C.load(root)
    return {
        cls
        for comp in C.components(cfg)
        for cls in C.object_ref_classes(cfg, comp)
    }


def test_every_joiner_site_ran_in_the_fixture(tree):
    """The checks below are only as good as the faces the fixture reaches."""
    _, calls = tree
    ran = {(f, line) for f, line, _, _ in calls}
    missing = [
        f"{f.relative_to(_SRC.parent)}:{first}"
        for f, first, last in _sites()
        if not any(g == f and first <= ln <= last for g, ln in ran)
    ]
    assert not missing, (
        "these import-path sites never ran while the fixture was built, so "
        "nothing here checks what they write; widen `tree` until they do:\n  "
        + "\n  ".join(missing)
    )


def test_every_path_the_joiner_returned_holds_the_class(tree):
    """Checked where the path is COMPUTED, including what no file keeps."""
    root, calls = tree
    exports = _exports(root)
    pkg = C.project_name(C.load(root))
    bad = []
    for f, line, args, out in calls:
        site = f"{f.relative_to(_SRC.parent)}:{line}: {out!r}"
        if "Component" in args:  # class_import_line
            path, cls = re.fullmatch(r"from (\S+) import (\w+)", out).groups()
            if cls not in exports.get(path, set()):
                bad.append(site)
            continue
        # `pkg = ""` is the relative spelling: below the project package.
        full = out if args["pkg"] else ".".join(filter(None, (pkg, out)))
        if full not in exports:
            bad.append(f"{site} re-exports nothing")
    assert not bad, (
        "these computed a path that does not hold the class:\n  "
        + "\n  ".join(bad)
    )


_IMPORT_RE = re.compile(
    r"\bfrom\s+(\.+[\w.]*|[A-Za-z_][\w.]*)\s+import\s+(\w+)"
)


def _resolve(path: str, rel: Path) -> str:
    """*path* as an absolute dotted module, for a file at *rel* under src/."""
    if not path.startswith("."):
        return path
    dots = len(path) - len(path.lstrip("."))
    parts = list(rel.parent.parts)
    parts = parts[: len(parts) - (dots - 1)]
    tail = path[dots:]
    return ".".join(parts + ([tail] if tail else []))


def test_every_generated_import_names_where_the_class_lives(tree):
    """The issue's criterion, on the tree: no import names a wrong module."""
    root, _ = tree
    exports = _exports(root)
    classes = _classes(root)
    seen, bad = 0, []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel.parts[0] in ("build", ".git"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in _IMPORT_RE.finditer(line):
                where, cls = m.groups()
                if cls not in classes:
                    continue
                seen += 1
                if where.startswith(".") and rel.parts[0] != "src":
                    bad.append(f"{rel}:{n}: relative import outside src/")
                    continue
                if where.startswith("."):
                    where = _resolve(where, rel.relative_to("src"))
                if cls not in exports.get(where, set()):
                    bad.append(f"{rel}:{n}: {m.group(0)!r}")
    assert seen, "the scan found no import of any project class"
    assert not bad, (
        "these name a module that does not hold the class:\n  "
        + "\n  ".join(bad)
    )


def test_the_console_entry_point_names_the_module_it_wrote(tree):
    root, _ = tree
    entry = re.search(
        r'^\w+ = "([\w.]+):main"$',
        (root / "pyproject.toml").read_text(encoding="utf-8"),
        re.M,
    )
    assert entry, "no console entry point in pyproject.toml"
    mod = Path("src", *entry.group(1).split("."))
    assert (root / mod.with_suffix(".py")).is_file(), entry.group(1)


def test_the_contract_sits_beside_the_objects_test(tree):
    root, _ = tree
    tests = sorted((root / "src").rglob("test_o.py"))
    contracts = sorted((root / "src").rglob("test_o_invariants.py"))
    assert len(tests) == 1, tests
    assert [c.parent for c in contracts] == [tests[0].parent], contracts
