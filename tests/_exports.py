"""What a built ``lib<pkg>`` exports, read from the files the linker wrote.

The ONE reader behind every compiled "every export carries the prefix" gate
(tests/test_gh1591_c_prefix_nm.py, tests/test_gh1653_upgrade_builds.py).
Each file used to carry its own ``nm -g`` parse, and both were skipped on
Windows for the same reason; a second copy is the one a platform branch
lands in and the other does not (gh-1648).

Per platform, the two artifacts and how each is read:

- Linux: ``lib<pkg>.so.X.Y.Z`` by ``nm -g -D``; ``lib<pkg>.a`` by ``nm -g``.
- macOS: ``lib<pkg>.X.Y.Z.dylib`` and ``lib<pkg>.a``, both by ``nm -g``,
  Mach-O's leading ``_`` dropped.
- Windows (clang-cl): ``<pkg>.dll`` by ``llvm-readobj --coff-exports``;
  ``<pkg>_static.lib`` by ``llvm-nm -g``.

Windows is not ``nm`` over a different file. A DLL carries no symbol table:
``nm -g`` and ``llvm-nm -g`` both print ``no symbols`` over it and exit 0,
so an ``nm`` reader there passes having read nothing. What a consumer can
link is the DLL's EXPORT TABLE, which ``llvm-readobj --coff-exports`` prints
one ``Name:`` line per entry. The import library ``<pkg>.lib`` is not it
either: its members are ``__imp_<name>`` / ``<name>`` thunk pairs and
``__IMPORT_DESCRIPTOR_<pkg>`` records, a restatement of the table rather
than the table. (``llvm-objdump --coff-exports``, which gh-1648 suggested,
is not an option the runner's LLVM has.)

The static ``.lib`` is an ordinary archive of COFF objects and ``llvm-nm``
reads it as ``nm`` reads a ``.a``, with one difference measured on a
clang-cl build: MSVC's ABI puts literal pools in named COMDAT sections
with EXTERNAL linkage -- ``??_C@_05JBLEAKEJ@0?41?40?$AA@`` is the string
``"0.1.0"`` in ``<pkg>_lib.c`` (and the ABI names a float constant the
same way, ``__real@<hex>``). None is a C identifier, so none can be a name jm or the
author declared, and none reaches the DLL's export table (CMake's
``WINDOWS_EXPORT_ALL_SYMBOLS`` leaves them out). They are dropped by that
rule -- "not a C identifier" -- rather than by a list of MSVC's prefixes.

Every read asserts that it found something: a reader pointed at the wrong
file, or a tool that answers ``no symbols``, must go red here and not pass
the prefix check vacuously. A missing tool FAILS, never skips.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"

#: A name C source can declare. A COFF literal pool's name cannot be one.
_C_IDENT = re.compile(r"[A-Za-z_]\w*\Z")


def _run(*cmd: object) -> str:
    """*cmd*'s stdout; a non-zero exit fails the calling test with its
    output."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-2000:], r.stderr[-2000:])
    return r.stdout


def _tool(name: str) -> str:
    """*name*'s path. Asserted, not skipped: the leg that builds a library
    has the toolchain that reads it, so an absent one is a broken leg."""
    path = shutil.which(name)
    assert path, f"{name} is required on this host"
    return path


def _nm_defined(nm_out: str) -> "set[str]":
    """Defined global symbols from ``nm -g`` / ``llvm-nm -g`` output.

    Undefined (``U``) and weak-undefined (``w``, ``v``) are not the
    library's exports. Mach-O's leading underscore is dropped, and on
    Windows a name that is not a C identifier -- an MSVC literal pool,
    see the module docstring -- is not counted.
    """
    out = set()
    for line in nm_out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] not in ("U", "w", "v"):
            name = parts[2]
            if sys.platform == "darwin" and name.startswith("_"):
                name = name[1:]
            if WINDOWS and not _C_IDENT.match(name):
                continue
            out.add(name)
    return out


def _coff_exports(readobj_out: str) -> "set[str]":
    """The names in ``llvm-readobj --coff-exports`` output: one
    ``Export { Ordinal: N  Name: <sym>  RVA: 0x... }`` block per entry.
    An export by ordinal alone has no ``Name:`` and names nothing."""
    return set(re.findall(r"^\s*Name:\s*(\S+)\s*$", readobj_out, re.M))


def artifacts(build_dir: Path, pkg: str) -> "tuple[Path, Path]":
    """``(static, shared)``: the two ``lib<pkg>`` files *build_dir* holds.

    Exactly one of each. The shared library's versioned links (``.so``,
    ``.so.X.Y``, the ``.dylib`` pair) are symlinks to the one real file.
    """
    if WINDOWS:
        static = list(build_dir.rglob(f"{pkg}_static.lib"))
        shared = list(build_dir.rglob(f"{pkg}.dll"))
    else:
        static = list(build_dir.rglob(f"lib{pkg}.a"))
        shared = [
            p
            for p in build_dir.rglob(f"lib{pkg}.*")
            if p.is_file()
            and not p.is_symlink()
            and (".so" in p.suffixes or p.suffix == ".dylib")
        ]
    found = sorted(build_dir.rglob(f"*{pkg}*"))
    assert len(static) == 1 and len(shared) == 1, (static, shared, found)
    return static[0], shared[0]


def static_exports(lib: Path) -> "set[str]":
    """Defined globals of the static library *lib*."""
    syms = _nm_defined(_run(_tool("llvm-nm" if WINDOWS else "nm"), "-g", lib))
    assert syms, f"no defined global read from {lib}"
    return syms


def shared_exports(lib: Path) -> "set[str]":
    """What the shared library *lib* exports to a program that links it:
    the dynamic symbol table (ELF), the external symbols (Mach-O), or the
    export table (a Windows DLL)."""
    if WINDOWS:
        out = _run(_tool("llvm-readobj"), "--coff-exports", lib)
        syms = _coff_exports(out)
    else:
        dyn = ["-D"] if sys.platform.startswith("linux") else []
        syms = _nm_defined(_run(_tool("nm"), "-g", *dyn, lib))
    assert syms, f"no export read from {lib}"
    return syms


def exports(build_dir: Path, pkg: str) -> "set[str]":
    """Every symbol ``lib<pkg>`` defines for a consumer, static and shared,
    from the build in *build_dir*."""
    static, shared = artifacts(build_dir, pkg)
    return static_exports(static) | shared_exports(shared)
