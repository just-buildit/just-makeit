- **A consumer of your installed library can compile your headers when they
    include a dependency's** (gh-1576). nco_tone's `tone_core.h` includes
    doppler's `nco/nco_core.h`, and four of the eight ways to consume such a
    project failed with `fatal error: nco/nco_core.h: No such file or   directory`.
    - **CMake:** the shared library links a dependency PRIVATE, so it passed
        on none of it. It now passes on the dependency's include dirs,
        definitions and options without re-linking it. That is
        `$<COMPILE_ONLY:>`, written out so it works at the CMake 3.16 floor,
        and measured identical on 3.16 and 3.28.
    - **pkg-config:** a `[project] find_packages` entry may now be a table,
        `{ name = "Doppler", pkg_config = "doppler" }`. The module name goes to
        the installed `.pc`'s `Requires.private`, which carries its Cflags
        always and its Libs with `--static`. `{ name, libs_private = "..." }`
        covers a dependency that ships no `.pc`, via `Libs.private`. A bare
        string still works through `find_package`. `jm status` lists it under
        PKG-CONFIG, because the `.pc` cannot name it.
    - The manifest writer no longer quotes a table inside a `[project]` list.
    - `docs/c-library.md` has a new section, "When your library depends on
        another package". It also drops the consumer's hand-written `-lm`,
        which the package has carried itself since gh-1452.
