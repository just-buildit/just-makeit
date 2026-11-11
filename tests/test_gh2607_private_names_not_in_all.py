"""gh-2607: a name starting with ``_`` is imported, never listed in ``__all__``.

``__all__`` is a package's public list: what ``from pkg import *`` binds and
what a documentation index reads as the public surface. doppler's
``wfm/__init__.py`` listed ``_SynthEngine`` (an object whose ``class_name``
starts with ``_``) and ``_sweep_ends`` (a module function), so its A-Z
index showed both as public (doppler-dsp/doppler#3000).

GATE: in a project holding a private name on every path that writes an
``__all__`` -- a module object, a module function, a ``reexports`` name, a
platform-restricted sibling module's object (its ``__all__ +=`` guard), a
standalone object in the package ``__init__.py`` -- no ``__all__`` in ANY
generated ``__init__.py`` lists a private name, every private name is still
imported, and every public one is still listed. Registration-free over
files: every ``__init__.py`` under ``src/`` is read, so a new writer of one
is held too. And an ``__all__`` the author laid out (one jm does not
re-emit) is not handed a private name either.

Sabotage (scripts/sabotage.py) each site and this goes red:
``_object._fmt_all``'s filter (every re-emitted ``__all__``),
``_modplatforms._block``'s ``__all__ +=`` (a guard of only private names
must write none), ``_init._splice_init_py``'s author-``__all__`` branch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit._init import _splice_init_py

#: The private names the fixture declares, each on its own path.
PRIVATE = {"_Eng", "_sweep", "_b", "_Snk", "_Priv"}
#: The public names beside them, which must stay listed.
PUBLIC = {"Mix", "a", "Pub"}


def _jm(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gh2607")
    _jm("new", "pv", cwd=root)
    proj = root / "pv"
    for args in (
        ("module", "sig"),
        ("object", "mix", "--module", "sig"),
        ("object", "eng", "--module", "sig", "--class-name", "_Eng"),
        (
            "function",
            "_sweep",
            "--module",
            "sig",
            "--param",
            "x:double",
            "--return-type",
            "double",
        ),
        ("module", "sink"),
        ("object", "snk", "--module", "sink", "--class-name", "_Snk"),
        ("object", "pub"),
        ("object", "priv", "--class-name", "_Priv"),
    ):
        _jm(*args, cwd=proj)
    mods = proj / "modules"
    with (mods / "sink.toml").open("a") as f:
        # A sibling sharing `sig`'s package, built on two platforms only:
        # its names go in a `# jm:platforms` guard (gh-1463).
        f.write('package = "sig"\nplatforms = ["linux", "macos"]\n')
    with (mods / "sig.toml").open("a") as f:
        f.write('\n[module.sig.reexports]\ncic = ["a", "_b"]\n')
    _jm("apply", cwd=proj)
    return proj


def _read(path: Path) -> "tuple[set[str], set[str], list[str]]":
    """(names in any ``__all__``, names imported relatively, the source of
    each ``__all__`` statement) for one ``__init__.py`` -- at any depth, so
    a platform guard's ``__all__ +=`` is read too."""
    listed: set[str] = set()
    imported: set[str] = set()
    stmts: list[str] = []
    text = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.level:
            imported |= {a.asname or a.name for a in node.names}
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AugAssign)
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            stmts.append(ast.get_source_segment(text, node) or "")
            value = node.value  # type: ignore[attr-defined]
            listed |= {
                e.value
                for e in getattr(value, "elts", [])
                if isinstance(e, ast.Constant)
            }
    return listed, imported, stmts


def test_no_private_name_in_any_all(project: Path) -> None:
    inits = sorted((project / "src").rglob("__init__.py"))
    assert inits
    listed: set[str] = set()
    imported: set[str] = set()
    for init in inits:
        names, imports, stmts = _read(init)
        leaked = sorted(n for n in names if n.startswith("_"))
        assert not leaked, f"{init}: __all__ lists {leaked}"
        # A guard holding only private names writes no `__all__ +=` at all.
        assert not any(s.replace(" ", "").endswith("+=[]") for s in stmts), (
            f"{init}: an empty `__all__ +=`\n{init.read_text()}"
        )
        listed |= names
        imported |= imports
    assert PRIVATE <= imported, f"not imported: {PRIVATE - imported}"
    assert PUBLIC <= listed, f"not listed: {PUBLIC - listed}"


def test_status_clean_after_apply(project: Path) -> None:
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr


def test_author_all_not_handed_a_private_name(tmp_path: Path) -> None:
    # An `__all__` with a comment is the author's to lay out: jm splices a
    # public name into it in place, and a private one not at all.
    init = tmp_path / "__init__.py"
    init.write_text(
        "from .pub import Pub  # noqa: E402\n\n"
        '__all__ = ["Pub"]  # the public surface\n',
        encoding="utf-8",
    )
    _splice_init_py(init, "priv", "_Priv")
    text = init.read_text(encoding="utf-8")
    assert "from .priv import _Priv" in text
    assert '__all__ = ["Pub"]  # the public surface' in text
    _splice_init_py(init, "other", "Other")
    assert '"Other"' in init.read_text(encoding="utf-8")
