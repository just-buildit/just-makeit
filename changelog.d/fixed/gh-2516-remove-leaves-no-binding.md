- **Removing a module object takes its binding fragments with it, and a
    module left with no export stops importing one** (gh-2516). `jm remove   object` left `<module>_ext_<obj>.c` and each of its views' fragments on
    disk, included by nothing; and when the object was the module's last
    export, its `__init__.py` kept `from .<module> import <Class>`, which
    raised `ImportError` at import. Both are gone now: the fragments are
    removed, and the import line goes with the last name, back to the
    scaffold's `__all__ = []`.
