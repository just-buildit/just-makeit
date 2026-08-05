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


def __getattr__(name: str) -> str:
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            resolved = version("just-makeit")
        except PackageNotFoundError:
            resolved = "unknown"
        globals()["__version__"] = resolved
        return resolved
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    """Keep ``__version__`` visible to `dir()` despite being lazy."""
    return sorted(set(globals()) | {"__version__"})
