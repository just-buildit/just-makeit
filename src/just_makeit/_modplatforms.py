"""Which platforms a module's extension is built on -- ``[module.X] platforms``.

gh-1463. A module over a platform-conditional core cannot configure where the
core does not exist. doppler's NATS stream cores are POSIX-only and defined
under ``if(NOT WIN32)``; a ``kind = "handle"`` module embedding them
referenced ``$<TARGET_OBJECTS:stream_core_obj>`` unconditionally, and on
Windows a dangling ``$<TARGET_OBJECTS:>`` fails CMake's GENERATE step, so no
extension built at all. Neither face could be fixed downstream: the module
CMake is regenerated (its ``_extra.cmake`` hook runs after the target exists,
too late), and so is the re-export in the owning package's ``__init__.py`` --
skipping the build there would have turned ``import doppler.wfm`` into an
ImportError on Windows for the sake of one name.

So the author states it, and jm renders it on BOTH faces from this one file:

- **CMake** -- the module's ``if(BUILD_PYTHON)`` becomes
  ``if(BUILD_PYTHON AND (<platform test>))``, for every module kind
  (plain, handle, capsule, composer), via :func:`cmake_guard`.
- **Python** -- wherever the module's names are imported into an
  ``__init__.py`` (its own, or the owning package's ``reexports``), the import
  and the names' ``__all__`` entry move into a block that runs only on the
  declared platforms, via :func:`guard_init`. Elsewhere the names are ABSENT,
  not present and broken.

Declared rather than inferred. ``if(TARGET ...)`` keyed on the
``$<TARGET_OBJECTS:...>`` entries would also fix the CMake face, but it keys
on what happens to be missing rather than on what the author meant, and it
says nothing about the re-export.

The key reuses the vocabulary of the RETIRED ``[project] platforms``
(``linux``, ``macos``, ``windows``) because the words mean the same thing; the
retired key changed what the whole project emitted, and this one scopes a
single module.

**What it does not scope** is a plain module's C cores: the OBJECT libraries
its objects compile to sit outside the ``BUILD_PYTHON`` block, and feed
``lib<pkg>`` from the root CMakeLists, so they build everywhere. A
platform-conditional core is the author's to guard, as doppler's already is.
"""

from __future__ import annotations

import re

#: The platforms a module may name, each as (CMake test, ``sys.platform``).
#: The order is the canonical order every rendering uses, so a manifest that
#: lists them differently renders byte-identically.
PLATFORMS: "dict[str, tuple[str, str]]" = {
    "linux": ('CMAKE_SYSTEM_NAME STREQUAL "Linux"', "linux"),
    "macos": ("APPLE", "darwin"),
    "windows": ("WIN32", "win32"),
}


def _declared(cfg: dict, module: str) -> "object":
    return ((cfg.get("module") or {}).get(module) or {}).get("platforms")


def errors(cfg: dict) -> "list[str]":
    """Every ``[module.X] platforms`` that does not name platforms jm knows.

    Refused at load rather than ignored: a misspelt entry would silently
    drop that platform from the build, and an empty list would build the
    module nowhere -- neither is something a user writes on purpose.

    >>> errors({"module": {"s": {"platforms": ["linux", "macOS"]}}})
    ['[module.s] platforms: unknown platform "macOS" (known: linux, macos, windows)']
    >>> errors({"module": {"s": {"platforms": []}}})
    ['[module.s] platforms is empty; list at least one of linux, macos, windows, or remove the key']
    >>> errors({"module": {"s": {"platforms": ["linux"]}}})
    []
    """
    out: "list[str]" = []
    for mod, data in sorted((cfg.get("module") or {}).items()):
        if not isinstance(data, dict) or "platforms" not in data:
            continue
        out += list_errors(f"[module.{mod}]", data["platforms"])
    return out


def list_errors(where: str, raw: object) -> "list[str]":
    """Why a ``platforms`` list declared at *where* is not one jm can build.

    One check for every table that scopes itself to platforms -- a module's
    extension (gh-1463) and an additional library (gh-1600).

    >>> list_errors("[project.libraries.x]", ["linux"])
    []
    """
    known = ", ".join(PLATFORMS)
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        return [
            f"{where} platforms must be a list of strings, "
            f'e.g. ["linux", "macos"]'
        ]
    out: "list[str]" = []
    if not raw:
        out.append(
            f"{where} platforms is empty; list at least one of "
            f"{known}, or remove the key"
        )
    for p in raw:
        if p not in PLATFORMS:
            out.append(
                f'{where} platforms: unknown platform "{p}" (known: {known})'
            )
    return out


def canonical(raw: object) -> "tuple[str, ...] | None":
    """A declared ``platforms`` list, canonically ordered; ``None`` when it
    is absent or names every platform (so the full set is not churn).

    >>> canonical(["macos", "linux"]), canonical(None)
    (('linux', 'macos'), None)
    """
    if not isinstance(raw, list) or not raw:
        return None
    chosen = tuple(p for p in PLATFORMS if p in raw)
    return None if len(chosen) == len(PLATFORMS) else chosen


def platform_test(chosen: "tuple[str, ...]") -> str:
    """The CMake condition true on exactly the *chosen* platforms.

    >>> platform_test(("linux", "macos"))
    'CMAKE_SYSTEM_NAME STREQUAL "Linux" OR APPLE'
    """
    return " OR ".join(PLATFORMS[p][0] for p in chosen)


def module_platforms(cfg: dict, module: str) -> "tuple[str, ...] | None":
    """The platforms *module* is restricted to, canonically ordered.

    ``None`` means unrestricted: the key is absent, or it names every
    platform -- which must render exactly as absent, so declaring the full
    set is not churn.

    >>> module_platforms({"module": {"s": {"platforms": ["macos", "linux"]}}}, "s")
    ('linux', 'macos')
    >>> module_platforms({"module": {"s": {}}}, "s") is None
    True
    >>> module_platforms(
    ...     {"module": {"s": {"platforms": ["windows", "linux", "macos"]}}}, "s"
    ... ) is None
    True
    """
    return canonical(_declared(cfg, module))


def cmake_guard(cfg: dict, module: str) -> str:
    """The condition a module's extension target is built under.

    >>> cmake_guard({"module": {"s": {}}}, "s")
    'BUILD_PYTHON'
    >>> cmake_guard({"module": {"s": {"platforms": ["linux", "macos"]}}}, "s")
    'BUILD_PYTHON AND (CMAKE_SYSTEM_NAME STREQUAL "Linux" OR APPLE)'
    """
    chosen = module_platforms(cfg, module)
    if chosen is None:
        return "BUILD_PYTHON"
    return f"BUILD_PYTHON AND ({platform_test(chosen)})"


def init_platforms(
    cfg: dict, module: str
) -> "dict[str, tuple[str, ...] | None]":
    """The ``__init__.py`` import lines *module*'s merge owns, by leaf.

    Its own line (``from .<leaf> import ...``) and one per ``reexports``
    entry, each mapped to the platforms of the module that builds that
    submodule. A reexport naming something no module builds -- a hand-written
    ``.py`` sibling -- is unrestricted.

    Every owned leaf is listed, restricted or not, because a leaf that STOPS
    being restricted must have its guarded block removed; :func:`guard_init`
    can only do that for a leaf it is told about.
    """
    from . import _config as C

    def _out_pkg(mod: str) -> str:
        return C.module_package(cfg, mod) or C.module_paths(mod).pypath

    here = _out_pkg(module)
    by_leaf = {
        C.module_paths(m).leaf: module_platforms(cfg, m)
        for m in C.modules(cfg)
        if _out_pkg(m) == here
    }
    owned = {C.module_paths(module).leaf: module_platforms(cfg, module)}
    for sub in C.module_reexports(cfg, module):
        owned[sub] = by_leaf.get(sub)
    return owned


def _marker(leaf: str) -> str:
    return f"# jm:platforms {leaf}"


def _block_re(leaf: str) -> "re.Pattern[str]":
    # The marker line, the `if`, then every indented line after it -- which
    # is what a formatter that wraps the import or the `__all__ +=` list into
    # a parenthesised block still produces -- and any blank line INSIDE the
    # body (one followed by another indented line): ruff puts one after the
    # import, and stopping there orphaned the `__all__ +=` line, which the
    # next apply then wrote a second time. Preceding blank lines go with it,
    # so removing and re-inserting a block does not accumulate them.
    return re.compile(
        rf"\n*^{re.escape(_marker(leaf))}\b[^\n]*\n"
        r"if [^\n]*:\n"
        r"(?:[ \t]+[^\n]*\n|\n(?=[ \t]+\S))*",
        re.MULTILINE,
    )


def _block(leaf: str, names: "list[str]", chosen: "tuple[str, ...]") -> str:
    from ._object import _fmt_from_import

    # Double quotes, as a project's formatter would write them; a string it
    # rewrites is drift on every apply.
    platforms = ", ".join(f'"{PLATFORMS[p][1]}"' for p in chosen)
    if len(chosen) == 1:
        platforms += ","
    quoted = ", ".join(f'"{n}"' for n in names)
    return (
        f"{_marker(leaf)} -- built on {', '.join(chosen)} only (gh-1463)\n"
        f'if __import__("sys").platform in ({platforms}):\n'
        f"    {_fmt_from_import(leaf, names)}\n"
        # The blank line is the formatter's (ruff puts one after an import in
        # a nested block); writing it here makes `ruff format` a no-op.
        f"\n"
        f"    __all__ += [{quoted}]\n"
    )


def guard_init(text: str, owned: "dict[str, tuple[str, ...] | None]") -> str:
    """Move each restricted leaf's import and ``__all__`` names into a guard.

    Runs after the ordinary merge, which knows nothing of platforms: it writes
    every owned leaf's import at column 0 and its names into ``__all__``, as
    it always has. For a restricted leaf this lifts both out again -- the
    import line is removed, its names leave the ``__all__`` literal, and a
    marked block after ``__all__`` imports them only on the declared
    platforms. For an unrestricted leaf it removes any block left from when
    it was restricted. Idempotent, because the merge re-derives the column-0
    line every time and this re-derives the block from it.

    >>> src = ('from .core import A  # noqa: E402\\n'
    ...        'from .sink import S  # noqa: E402\\n'
    ...        '\\n'
    ...        '__all__ = ["A", "S"]\\n')
    >>> once = guard_init(src, {"core": None, "sink": ("linux", "macos")})
    >>> print(once)
    from .core import A  # noqa: E402
    <BLANKLINE>
    __all__ = ["A"]
    <BLANKLINE>
    # jm:platforms sink -- built on linux, macos only (gh-1463)
    if __import__("sys").platform in ("linux", "darwin"):
        from .sink import S  # noqa: E402
    <BLANKLINE>
        __all__ += ["S"]
    <BLANKLINE>

    Running it again changes nothing, and lifting the restriction removes
    the block (the merge has already written the plain line back):

    >>> guard_init(once, {"core": None, "sink": ("linux", "macos")}) == once
    True
    >>> print(guard_init(once, {"sink": None}))
    from .core import A  # noqa: E402
    <BLANKLINE>
    __all__ = ["A"]
    <BLANKLINE>
    """
    from ._object import _ALL_RE, _fmt_all, _import_re, _parse_all_names
    from ._object import _parse_import_names

    for leaf, chosen in owned.items():
        block_re = _block_re(leaf)
        if chosen is None:
            text = block_re.sub("\n", text, count=1)
            continue
        m = _import_re(leaf).search(text)
        if not m:
            # Nothing at column 0 to absorb: the merge wrote no line for this
            # leaf this time, so whatever block exists is still current.
            continue
        names = _parse_import_names(m.group(0))
        start = m.start()
        end = m.end() + (1 if text[m.end() : m.end() + 1] == "\n" else 0)
        text = text[:start] + text[end:]
        # The merge inserts a line it had to ADD as `<line>\n\n` ahead of
        # `__all__`; lifting the line out must take that blank line with it,
        # or every apply leaves one more behind.
        while text[start : start + 1] == "\n" and text.endswith(
            "\n\n", 0, start
        ):
            text = text[:start] + text[start + 1 :]
        am = _ALL_RE.search(text)
        if am:
            kept = [n for n in _parse_all_names(am.group(1)) if n not in names]
            text = text[: am.start()] + _fmt_all(kept) + text[am.end() :]
        block = _block(leaf, names, chosen)
        if block_re.search(text):
            text = block_re.sub(lambda _: "\n\n" + block, text, count=1)
        else:
            am = _ALL_RE.search(text)
            if am:
                # After the rest of the `__all__` line (a formatter's
                # trailing comment, say), so the block is its own statement.
                eol = text.find("\n", am.end())
                eol = len(text) if eol < 0 else eol + 1
                text = text[:eol] + "\n" + block + text[eol:]
            else:
                text = text.rstrip("\n") + "\n\n" + block
    return text
