- **A published `[[enum]]` table has a docstring in the `.pyi`** (gh-2606).
    The module attribute an `[[enum]]` is published under (gh-2344) was a
    bare annotation, and a documentation tool such as mkdocstrings hides an
    attribute with no docstring, so a downstream API reference left every
    published table out. An `[[enum]]` row now takes a `doc` key, written
    as the attribute docstring right after the annotation; without one, the
    docstring is a generated sentence naming the `[[enum]]` and its shape (a
    dense tuple, or a dict keyed by the C constants it names). A misspelt
    key on an `[[enum]]` row is now reported like any other unknown manifest
    key. The runtime value is a plain `tuple` / `dict`, which cannot carry a
    docstring of its own.
