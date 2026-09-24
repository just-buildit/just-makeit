- **The installed `.pc` now lists `[project] pkg_modules` as
    `Requires.private`** (gh-1573). gh-1572 made the static library carry a
    core's external link dependencies for `find_package` consumers. The `.pc`
    still said only `-l<pkg> -lm`, though, so `pkg-config --static --libs`
    left the archive's calls into the dependency undefined. A `pkg_modules`
    entry is a pkg-config module name, so it now goes to `Requires.private`,
    which `--static` follows. A `find_packages` entry has no pkg-config name
    jm can derive, so it is not listed; `docs/c-library.md` says to declare
    the dependency with `pkg_modules` when the pkg-config face matters. An
    existing project's `cmake/<pkg>.pc.in` is reported OUTDATED until it is
    refreshed.
