- **Both installers install patchelf on Linux, and `install-deps --check`
    reports it missing** (gh-1972). auditwheel needs patchelf to repair a
    Linux wheel. `just-makeit install-deps` listed it for every Linux
    package manager but checked only for cmake and a C compiler, so on a
    box with both and no patchelf `--check` printed "All build dependencies
    are already installed." and exited 0, and a run installed nothing. The
    curl installer, `install.sh`, never installed it at all. patchelf is now
    a dependency of its own on Linux in both: `--check` names it and exits
    1, a run installs it, and on a distro neither knows the hint names it
    among the packages to install by hand. macOS is not asked for it, since
    delocate repairs a macOS wheel without it.
