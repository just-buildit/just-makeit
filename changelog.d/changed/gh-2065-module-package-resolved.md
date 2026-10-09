- **Internal: one owner of where a module's Python lives** (gh-2065). The
    answer `_config.module_package_resolved` gives (gh-2054) -- a module's
    `package` when declared, else its own path -- was also spelled inline
    as `module_package(...) or ...pypath` at 20 sites across ten modules,
    and that pair drifting apart is what gh-2054 was. They all read the
    owner now. A source gate refuses a new inline spelling, with one named
    exception: the re-export `__init__.py` writer, which gh-2054's gate
    uses as its independent oracle. Generated projects are byte-identical
    to before.
