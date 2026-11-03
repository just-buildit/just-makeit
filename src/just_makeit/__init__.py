"""just-makeit — Python C extensions the easy way."""

# gh-764: `__version__` is resolved on first access, not at import.
#
# `importlib.metadata.version()` cost ~25.5 ms of the CLI's ~29 ms import — it
# drags in `importlib.metadata`, `email`, `inspect` and `zipfile` — and every
# jm invocation paid it, including `jm --help` and the query commands that
# never ask what version they are. The readers that do (`jm version`,
# `_config.jm_cli_version`, the manifest stamp) already import at their call
# sites, so deferring is invisible to them.
#
# PEP 562: a module-level `__getattr__` runs only for names *not* found in the
# module namespace, so the first access caches into globals() and every later
# one is a plain attribute lookup.

#
# gh-2452: the rest of the public API that lives in a private module is
# re-exported the same lazy way, one row per name: the module that DEFINES
# it, so `just_makeit.<name> is <module>.<name>` -- one implementation, not a
# copy -- and `_config`'s import cost is paid only by a caller who asks.
# `tests/test_gh2452_public_api.py` holds the identity.
#
# ``just_makeit.components(cfg)`` -- `_config.components`: a manifest's
#     component names, every top-level key but the reserved sections and
#     ``include``.
# ``just_makeit.Refusal`` -- `_report.Refusal`: the ``ValueError`` jm raises
#     when it refuses a manifest, carrying each problem in ``.errors``.
_REEXPORTS = {
    "components": "_config",
    "Refusal": "_report",
}


def __getattr__(name: str) -> object:
    if name in _REEXPORTS:
        from importlib import import_module

        value = getattr(import_module(f".{_REEXPORTS[name]}", __name__), name)
        globals()[name] = value
        return value
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            resolved = version("just-makeit")
        except PackageNotFoundError:
            # gh-1166: return the fallback WITHOUT memoising it. The cache is
            # for the life of the process and has no invalidation, so caching
            # a failure makes a transient condition permanent -- metadata not
            # yet installed (an editable install mid-sync, a frozen build), or
            # a test that patches `importlib.metadata.version` while some
            # other test happens to take the first look. That last one is not
            # hypothetical: it made `test_gh764`'s
            # `__version__ == C.jm_cli_version()` fail under some random
            # orderings and pass under others, because `jm_cli_version()`
            # re-imports and gets the real value while this cache still held
            # "unknown". Only a successful resolution is stable enough to keep.
            return "unknown"
        globals()["__version__"] = resolved
        return resolved
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_manifest(root) -> dict:
    """Read a project's merged manifest: the root plus every fragment.

    This is the public, stable way for a project's own scripts to read
    ``just-makeit.toml`` (gh-2440). Under the fragment layout the root
    file holds only ``[project]`` and an ``include`` list of globs, and
    each ``[<obj>]`` / ``[module.X]`` table lives in ``objects/*.toml`` /
    ``modules/*.toml``; a ``tomllib`` parse of the root alone finds none
    of them. This function returns the manifest every jm command works
    from -- the same dict ``jm apply`` renders -- because it IS jm's own
    reader, not a second merge.

    What that reader does, in order:

    1. Parses ``just-makeit.toml`` and removes its ``include`` key.
    2. Merges each file the ``include`` globs name, sorted by path:
       component tables are added, a ``[module.X]`` is set once (a
       conflicting redefinition is refused), and
       ``[[module.X.functions]]`` rows concatenate across fragments.
    3. Expands every ``[template.X]`` into its instance components and
       every ``[[<obj>.init_groups]]`` into real ``init_params`` rows.
    4. Resolves an omitted ``[project] version`` from ``pyproject.toml``.
    5. Validates the result, refusing a manifest jm would refuse.

    A key whose name starts with ``_`` (``_template``, ``_group``,
    ``_version_deferred``, ...) is a marker jm adds while loading; it is
    not part of the manifest schema and may change between releases.

    Parameters
    ----------
    root : str or os.PathLike
        The project root: the directory holding ``just-makeit.toml``.

    Returns
    -------
    dict
        The merged manifest, as plain TOML values (``dict``, ``list``,
        ``str``, ``int``, ``float``, ``bool``). The dict is the caller's:
        built afresh on every call, so mutating it changes neither the
        project nor a later call's result. It is never written back.

    Raises
    ------
    FileNotFoundError
        If *root* holds no ``just-makeit.toml``.
    ValueError
        If the manifest cannot be used. A file that is not valid TOML
        raises ``tomllib.TOMLDecodeError``. A manifest jm refuses (a
        module declared in two fragments, a ``[project]`` table in a
        fragment, an unknown platform, ...) raises
        ``just_makeit.Refusal``, whose message names every problem,
        one per line, and whose ``.errors`` holds them as a tuple;
        nothing is printed. Both are ``ValueError`` subclasses.

    See Also
    --------
    ``just_makeit.components`` lists the result's component names.

    The CLI form, for tools that are not Python: ``just-makeit manifest
    --json`` prints this same dict as JSON on stdout.

    Examples
    --------
    A fragment-layout project whose module lives in ``modules/``:

    >>> import pathlib, shutil, tempfile
    >>> from just_makeit import load_manifest
    >>> root = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (root / "just-makeit.toml").write_text(
    ...     'include = ["modules/*.toml"]\\n'
    ...     '[project]\\nname = "demo"\\nversion = "0.1.0"\\n'
    ... )
    >>> (root / "modules").mkdir()
    >>> _ = (root / "modules" / "filter.toml").write_text(
    ...     '[module.filter]\\nobjects = []\\n'
    ... )
    >>> cfg = load_manifest(root)
    >>> sorted(cfg["module"])
    ['filter']
    >>> cfg["project"]["name"]
    'demo'
    >>> "include" in cfg
    False
    >>> cfg["module"].clear()
    >>> sorted(load_manifest(root)["module"])
    ['filter']
    >>> shutil.rmtree(root)
    """
    # Imported here, not at module level: `import just_makeit` is on every
    # CLI invocation's path, and `_config` is not cheap (gh-764).
    from pathlib import Path

    from . import _config

    root = Path(root)
    path = root / _config.FILENAME
    if not path.is_file():
        # `_config.load` reads a missing manifest as an empty one, which
        # is right for a jm command probing a directory and wrong for a
        # caller who named a project: an empty dict would read as "no
        # modules" and let a script pass on nothing.
        raise FileNotFoundError(f"no {_config.FILENAME} in {root}")
    cfg = _config.load(root)
    # No copy: `load` parses the files afresh on every call and keeps no
    # reference, so the dict is already the caller's.
    # `tests/test_gh2440_load_manifest.py` holds that property.
    return cfg


def __dir__() -> list:
    """Keep the lazy names visible to `dir()` before their first access."""
    return sorted(set(globals()) | {"__version__", *_REEXPORTS})
