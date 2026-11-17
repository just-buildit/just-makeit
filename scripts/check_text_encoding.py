#!/usr/bin/env python3
"""Fail on text file I/O that leaves the codec to the platform (gh-2654).

``Path.read_text()``, ``Path.write_text(s)`` and ``open(p, "w")`` with no
``encoding=`` use the locale's preferred encoding. On Linux and macOS that
is UTF-8, so the call works there and every review reads it as fine. On
Windows it is cp1252: #2651's test wrote a rendered header holding an em
dash with ``Path.write_text(text)``, and the one Windows leg that ran it
failed with ``UnicodeEncodeError: 'charmap' codec can't encode
characters``. Whether such a call works is a property of the TEXT (ASCII
or not), not of the call, so it passes until the day the content changes.

The rule is therefore held on the call, where it can fail on any host:
every text-mode open names its encoding.

ruff has this rule -- ``PLW1514`` (unspecified-encoding) -- and it was
measured first, on ruff 0.15.21 over this tree on 2026-11-17: it found 9
of the ~2000 calls this script finds. It flags a call only when it can
INFER the receiver is a ``pathlib.Path``, and a test's ``tmp_path``
fixture, a loop variable or a helper's return value is not inferable. It
is also preview-only, and turning preview on changed the verdict of stable
rules elsewhere (an ``F401`` appeared). So the call shape is read here, by
name, with no inference:

* ``<x>.read_text(...)``  -- needs ``encoding=`` or a positional encoding;
* ``<x>.write_text(...)`` -- needs ``encoding=`` or a second positional;
  ``_textio.write_text`` is jm's own UTF-8/LF writer and is exempt;
* ``open(...)``, ``io.open(...)``, ``<path>.open(...)`` and
  ``tempfile.*TemporaryFile(...)`` in TEXT mode -- a mode with no ``b``,
  or one that is not a literal -- need ``encoding=``. ``open`` on a module
  that is not a file opener (``os``, ``tarfile``, ``zipfile``...) is not a
  text open and is skipped.

Matching by name over-approximates: any ``.read_text()`` is taken for
``Path.read_text``. That is the intended trade. Every such API that exists
here takes ``encoding=``, and an explicit codec is never wrong.

What is not walked: a file that ruff's ``extend-exclude`` in
``pyproject.toml`` names (the Jinja templates, which are not Python until
rendered; the frozen stale-project tree, whose bytes are the test), and
any path given with ``--exclude`` -- the Makefile passes the vendored
``.py`` files, which are fixed in canonical and re-vendored, never here.

Usage::

    check_text_encoding.py [--exclude PATH ...] [FILE ...]
    # no FILE: every tracked .py

Exit 1 naming each call as ``path:line: call``, 0 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- 3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Receivers whose ``.open`` is not a text-file open: a module-level opener
#: of an archive, a descriptor or a URL.
NOT_A_TEXT_OPEN = frozenset(
    {"os", "tarfile", "zipfile", "gzip", "bz2", "lzma", "webbrowser"}
)

#: ``tempfile`` constructors, whose default mode is ``w+b``.
TEMPFILES = frozenset(
    {"TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile"}
)


def _kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_text_mode(mode: Optional[ast.expr], default: str) -> bool:
    """A literal mode without ``b`` is text; a non-literal one may be."""
    if mode is None:
        return "b" not in default
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return True


def _has_encoding(call: ast.Call, positional_at: int) -> bool:
    """``encoding=`` given, by keyword or at its positional index.

    ``**kwargs`` counts: the caller may well be forwarding one.
    """
    if any(k.arg in ("encoding", None) for k in call.keywords):
        return True
    return len(call.args) > positional_at


def violation(call: ast.Call) -> str:
    """The offending call's short spelling, or ``""`` if it is fine.

    Examples
    --------
    >>> def v(src):
    ...     return violation(ast.parse(src, mode="eval").body)
    >>> v("p.read_text()")
    '.read_text()'
    >>> v("p.read_text(encoding='utf-8')")
    ''
    >>> v("p.write_text(s)")
    '.write_text()'
    >>> v("p.write_text(s, 'utf-8')")
    ''
    >>> v("_textio.write_text(p, s)")
    ''
    >>> v("open(p, 'w')")
    "open('w')"
    >>> v("open(p, 'rb')")
    ''
    >>> v("p.open('a')")
    ".open('a')"
    >>> v("tarfile.open(fileobj=b)")
    ''
    >>> v("tempfile.NamedTemporaryFile('w')")
    "NamedTemporaryFile('w')"
    >>> v("tempfile.NamedTemporaryFile()")
    ''
    """
    f = call.func
    attr = f.attr if isinstance(f, ast.Attribute) else None
    recv = f.value if isinstance(f, ast.Attribute) else None
    recv_name = recv.id if isinstance(recv, ast.Name) else None

    if attr == "read_text":
        return "" if _has_encoding(call, 0) else ".read_text()"
    if attr == "write_text":
        if recv_name == "_textio" or _has_encoding(call, 1):
            return ""
        return ".write_text()"

    builtin_open = isinstance(f, ast.Name) and f.id == "open"
    if builtin_open or (attr == "open" and recv_name == "io"):
        # open(file, mode, buffering, encoding)
        mode = call.args[1] if len(call.args) > 1 else _kw(call, "mode")
        if _is_text_mode(mode, "r") and not _has_encoding(call, 3):
            return f"open({_spell(mode)})"
        return ""
    if attr == "open" and recv_name not in NOT_A_TEXT_OPEN:
        # Path.open(mode, buffering, encoding)
        mode = call.args[0] if call.args else _kw(call, "mode")
        if _is_text_mode(mode, "r") and not _has_encoding(call, 2):
            return f".open({_spell(mode)})"
        return ""
    name = attr or (f.id if isinstance(f, ast.Name) else None)
    if name in TEMPFILES:
        # (mode, buffering, encoding, ...)
        mode = call.args[0] if call.args else _kw(call, "mode")
        if _is_text_mode(mode, "w+b") and not _has_encoding(call, 2):
            return f"{name}({_spell(mode)})"
    return ""


def _spell(mode: Optional[ast.expr]) -> str:
    return "" if mode is None else ast.unparse(mode)


def violations(path: Path) -> List[str]:
    """``path:line: call`` for each unspecified-encoding call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            v = violation(node)
            if v:
                out.append(f"{path}:{node.lineno}: {v}")
    return sorted(out, key=lambda s: int(s.split(":")[1]))


def ruff_excludes(root: Path) -> List[str]:
    """The ``[tool.ruff] extend-exclude`` globs from ``pyproject.toml``."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data.get("tool", {}).get("ruff", {}).get("extend-exclude", []))


def _excluded(name: str, globs: List[str]) -> bool:
    for g in globs:
        g = g.rstrip("/")
        if name == g or name.startswith(g + "/") or fnmatch(name, g):
            return True
    return False


def tracked_python(excludes: List[str]) -> List[Path]:
    """Every tracked ``.py`` file, minus *excludes* and ruff's."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    globs = ruff_excludes(Path.cwd()) + excludes
    return [Path(n) for n in out.split("\0") if n and not _excluded(n, globs)]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("files", nargs="*", type=Path)
    args = ap.parse_args(argv)
    files = args.files or tracked_python(args.exclude)
    bad = [v for p in files for v in violations(p)]
    if bad:
        print(
            "text I/O without encoding= -- the codec is the platform's, "
            'cp1252 on Windows (gh-2654). Add encoding="utf-8":'
        )
        for v in bad:
            print(f"  {v}")
        print(f"{len(bad)} call(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
