- **A standalone object's changed `class_name` converges on one apply**
    (gh-2616). The package `__init__.py` kept `from .<comp> import Old`, an
    import the extension no longer satisfies, and every `jm apply` added
    one more `from .<comp> import New`, because only the first statement
    from the component was read; `jm status --check` reported the file
    `STALE` forever. `apply` now cuts jm's statements from a component in
    the manifest back to the names it renders, each once, and takes a
    dropped name out of a plain `__all__`.
