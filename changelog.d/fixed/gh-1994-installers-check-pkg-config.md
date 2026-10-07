- **Both installers check for pkg-config on every platform, and Homebrew
    installs it** (gh-1994). Both installers' help said they install
    pkg-config, and every Linux package manager's list carried it, but
    neither checked for it: it arrived only as a side effect of another
    missing dependency, and the Homebrew path never installed it at all. So
    on a box with cmake, a compiler and patchelf but no pkg-config,
    `install-deps --check` and `install.sh --check` both passed, and a
    project declaring `[project] pkg_modules` then failed at CMake
    configure. pkg-config is now a dependency of its own in both, on Linux
    and macOS: `--check` names it and exits 1, a run installs it (on macOS,
    `brew install pkg-config`), and on a distro neither knows the hint
    names it. A box with only `pkgconf` still needs it: CMake 3.16, a
    generated project's floor, looks for `pkg-config` alone, and every
    manager's package provides that name.
