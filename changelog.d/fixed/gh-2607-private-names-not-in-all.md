- **A name starting with `_` is imported by a generated `__init__.py` but
    never listed in its `__all__`** (gh-2607). jm listed every object and
    function it re-exported, so an object whose `class_name` is
    `_SynthEngine`, a module function `_sweep_ends` or a private
    `reexports` name reached `from pkg import *` and every tool that reads
    `__all__` as the public surface. Every `__all__` jm writes -- a
    module's, the package's, a platform guard's `__all__ +=` -- now leaves a
    private name out, from one predicate, and an `__all__` the author laid
    out is not handed one. The `from .<sub> import _SynthEngine` line
    stays, so the name is still importable by name. `jm status --check`
    reports an existing project's `__init__.py` `STALE` until the next
    `jm apply` drops the names.
