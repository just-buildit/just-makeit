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


def __dir__() -> list:
    """Keep ``__version__`` visible to `dir()` despite being lazy."""
    return sorted(set(globals()) | {"__version__"})
