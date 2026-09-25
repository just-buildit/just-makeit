- **A `pkg_modules` entry may carry a version bound, and a dependency with
    no `.pc` can put its compile flags in yours** (gh-1578, gh-1579).
    - `pkg_modules = ["zlib >= 1.2"]` failed at configure: the whole string
        became the `pkg_check_modules` prefix and module list, so CMake looked
        for a module named `>=`. The entry is now split into a name and a
        bound (`=`, `<`, `>`, `<=`, `>=`, pc(5)'s five). The name gives the
        prefix and the `PkgConfig::ZLIB` target. The root and the installed
        config pass `"zlib>=1.2"`, and the `.pc` lists `zlib >= 1.2` in
        `Requires.private`. `jm apply` and `jm new` refuse any other
        spelling before they write anything, including the `jm_version`
        stamp.
    - A `[project] find_packages` table entry takes `cflags` beside
        `libs_private`. A dependency that ships no `.pc` had nowhere to put
        its include flags, so a pkg-config consumer could not compile a header
        of yours that includes one of its headers. pc(5) has no private
        Cflags, so these go on the `.pc`'s own `Cflags` line. `jm status` no
        longer lists an entry with `cflags` under PKG-CONFIG, and its advice
        now names the key. An existing project's `cmake/<pkg>.pc.in` is
        reported OUTDATED until it is refreshed.
