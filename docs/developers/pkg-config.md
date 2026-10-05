# pkg-config & CMake Package Config: A Comprehensive Guide

A complete reference for shipping installable C libraries that consumers
can find with both `pkg-config` and CMake's `find_package`.

______________________________________________________________________

## Why Both?

Two different toolchain ecosystems consume installed C libraries:

| Tool         | Used by                                                       |
| ------------ | ------------------------------------------------------------- |
| `pkg-config` | Makefiles, Meson, Python cffi/ctypes, autoconf, shell scripts |
| CMake config | Any project using `find_package(MyLib REQUIRED)`              |

They are independent systems. Supporting both takes about 30 extra lines of
CMake and two small template files — always do both.

______________________________________________________________________

## File Layout

```
myproject/
├── CMakeLists.txt
└── cmake/
    ├── mylib.pc.in              # pkg-config template
    └── mylibConfig.cmake.in     # CMake find_package template
```

Keep the templates in `cmake/` next to the `CMakeLists.txt` that references
them. If your project uses subdirectories (e.g. a `c/` subdirectory for the
library), put `cmake/` there — CMake resolves the path relative to the
`CMakeLists.txt` that calls `configure_file`.

**In a jm project these are jm's files.** `cmake/<pkg>.pc.in` and
`cmake/<pkg>-config.cmake.in` are re-rendered whole by `jm apply` while their
first line is `# jm:generated` (`_createonly.RULES`; deleting that line makes
the template yours), and so is the root `CMakeLists.txt` from
`# ── Install` down to `# ── End install` (gh-1589) — put install rules of your
own below that block. A project scaffolded before gh-1589 has templates
without the token; `jm status` reports them, and `jm adopt --packaging` hands
them to jm.

______________________________________________________________________

## The pkg-config Template (`mylib.pc.in`)

```ini
prefix=@PC_PREFIX@
exec_prefix=${prefix}
libdir=@PC_LIBDIR@
includedir=@PC_INCLUDEDIR@

Name: mylib
Description: @PROJECT_DESCRIPTION@
URL: @PROJECT_HOMEPAGE_URL@
Version: @PROJECT_VERSION@
Requires: libzmq
Requires.private: libfftw3
Libs: -L${libdir} -lmylib
Libs.private: -lm -lpthread
Cflags: -I${includedir}
```

### Anatomy

- **`prefix`** — the absolute prefix the files were INSTALLED under, written
    at install time. `configure_file` leaves a marker
    (`prefix=%JM_INSTALL_PREFIX%`), and an `install(CODE)` declared before the
    `install(FILES)` replaces it with `${CMAKE_INSTALL_PREFIX}` as the install
    step sees it, so `cmake --install --prefix` is honoured. `DESTDIR` is not
    part of it: a staged tree names its real target. An absolute prefix is
    also what lets pkg-config drop `-I`/`-L` for its system dirs under
    `/usr`, which it does only for a literal path. A `${pcfiledir}`-relative
    prefix defeats that, so don't use one. Relocation belongs to the
    consumer: `pkg-config --define-prefix` for a moved tree,
    `PKG_CONFIG_SYSROOT_DIR` for a staged one (gh-1582).
- **`libdir` / `includedir`** — `${exec_prefix}/@CMAKE_INSTALL_LIBDIR@` when
    that is relative, and the path itself when it is absolute.
    `GNUInstallDirs` may be given absolute paths (Nix and Guix do), and the
    naive form then comes out as `${exec_prefix}//nix/store/.../lib`.
- **`Requires`** — public dependencies: consumers need them at link time.
- **`Requires.private`** — private dependencies: only needed when linking
    statically against your library. Omit from `Requires` to keep consumer
    link lines clean.
- **`Libs.private`** — same idea for `-l` flags. `-lm` and `-lpthread`
    usually belong here, not in `Libs` -- unless a public header calls into
    them inline. jm's headers do (`step()` is `static inline`), so jm puts
    `-lm` in `Libs` (gh-1452).

**What jm generates.** A manifest dependency with a `.pc` (a `pkg_modules`
entry, or a `find_packages` entry naming its `pkg_config`) goes in
`Requires.private` — its `Cflags` always apply (a jm header may include the
dependency's), its `Libs` only under `--static`. One without a `.pc` goes in
`Libs.private`, as the `libs_private` its entry states. The project's
`public_link_libs` and `public_defines` — what the installed headers need of
every consumer — go on `Libs:` and `Cflags:` (gh-1573, gh-1576, gh-1599; the
mapping is in `_apply.py`).

### Common Pitfalls

**Using absolute paths directly.**

```ini
# WRONG — not relocatable
libdir=/usr/local/lib
includedir=/usr/local/include

# RIGHT — the dirs follow the prefix, which is filled in at install
prefix=@PC_PREFIX@
libdir=${exec_prefix}/lib
includedir=${prefix}/include
```

**Baking in the configure-time `CMAKE_INSTALL_PREFIX`.**
`cmake --install --prefix` chooses the prefix at install time, so a
`.pc` written at configure time names a prefix where nothing was
installed. Write the prefix from an `install(CODE)`.

**A `${pcfiledir}`-relative prefix.** It survives a moved tree, but
pkg-config then cannot recognise `/usr` as a system prefix: every
consumer gets `-I/usr/include`, which breaks `#include_next`, and
`-L/usr/lib` ahead of its own `-L`. Moving a tree is what
`pkg-config --define-prefix` is for.

**Writing an optional field that may be empty.** `URL: @PROJECT_HOMEPAGE_URL@`
comes out as a bare `URL:` when `project()` has no `HOMEPAGE_URL`, and a slot
on a line of its own leaves a blank line, because `configure_file` ends every
output line with a newline. jm assembles the optional fields in CMake, each
ending its own line and only when set (`JM_PC_EXTRA_FIELDS`), and puts that
one slot at the start of the `Version:` line (gh-1582).

**Over-populating `Requires`.**
If your shared library links `libfftw3` with `PRIVATE` visibility, the
symbol is already resolved inside your `.so`. Consumer executables do not
need `-lfftw3` on their link line. Move it to `Requires.private` or omit
it entirely.

**Missing `Requires` for truly public deps.**
If a public header of yours `#include`s a header from another library,
that library belongs in `Requires` (or `Cflags` if header-only). The
consumer's compiler must find it.

______________________________________________________________________

## The CMake Config Template (`mylibConfig.cmake.in`)

```cmake
@PACKAGE_INIT@

include("${CMAKE_CURRENT_LIST_DIR}/mylibTargets.cmake")

check_required_components(mylib)
```

That is the complete correct minimal form. Do not add more unless you
have public CMake dependencies (see below).

### `@PACKAGE_INIT@`

This macro, provided by `CMakePackageConfigHelpers`, inserts the
`set_and_check()` and `check_required_components()` helpers and sets up
`PACKAGE_PREFIX_DIR` so relative paths work after install. Without it,
your config file will break whenever the install prefix changes.

### Public CMake Dependencies

If your public headers expose types from another CMake package, consumers
need to `find_package` that dependency too. Add it explicitly:

```cmake
@PACKAGE_INIT@

include(CMakeFindDependencyMacro)
find_dependency(ZeroMQ REQUIRED)    # public dep — headers expose zmq types
find_dependency(FFTW3)              # optional dep

include("${CMAKE_CURRENT_LIST_DIR}/mylibTargets.cmake")

check_required_components(mylib)
```

Use `find_dependency` (not `find_package`) inside config files — it
propagates `REQUIRED`/`QUIET` correctly to the caller.

### Common Pitfalls

**Using plain `configure_file` instead of `configure_package_config_file`.**

```cmake
# WRONG — paths baked in, not relocatable
configure_file(cmake/mylibConfig.cmake.in ...)

# RIGHT
configure_package_config_file(cmake/mylibConfig.cmake.in ...)
```

`configure_package_config_file` handles `PACKAGE_PREFIX_DIR` and the path
helper macros. Plain `configure_file` does string substitution only.

**Not using imported targets (the old variables pattern).**

```cmake
# OUTDATED — avoid
set(MYLIB_INCLUDE_DIRS "${PACKAGE_PREFIX_DIR}/include")
set(MYLIB_LIBRARIES    "${PACKAGE_PREFIX_DIR}/lib/libmylib.so")

# CORRECT — modern CMake, exported targets
include("${CMAKE_CURRENT_LIST_DIR}/mylibTargets.cmake")
# Consumer now uses:  target_link_libraries(app PRIVATE mylib::mylib)
```

Imported targets carry include paths, compile definitions and transitive
dependencies automatically. Consumers do not need to set anything manually.

______________________________________________________________________

## CMakeLists.txt Wiring

### Includes (top-level CMakeLists.txt)

```cmake
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
```

`GNUInstallDirs` defines `CMAKE_INSTALL_LIBDIR`, `CMAKE_INSTALL_INCLUDEDIR`,
`CMAKE_INSTALL_BINDIR` etc. as relative paths that follow platform
conventions. Always include it before any `install()` call.

### Build vs Install Include Paths

```cmake
target_include_directories(mylib
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)
```

The generator expressions give the build tree the source path and give
the installed package the install path. Without this, consumers who
call `find_package` get your source tree path baked into their builds —
a hard-to-debug failure on other machines.

### Install Rules

```cmake
# 1. Install the library and export the target
install(TARGETS mylib
    EXPORT mylibTargets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)

# 2. Install public headers
install(FILES include/mylib.h
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

# 3. Install the export set (the Targets.cmake file)
install(EXPORT mylibTargets
    FILE        mylibTargets.cmake
    NAMESPACE   mylib::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/mylib
)

# 4. Generate and install the Config and ConfigVersion files
configure_package_config_file(
    cmake/mylibConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/mylibConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/mylib
)

write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/mylibConfigVersion.cmake
    VERSION     ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)

install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/mylibConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/mylibConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/mylib
)

# 5. Generate the pkg-config file, and write its prefix at INSTALL time
#    (the prefix `cmake --install --prefix` chose; not DESTDIR)
set(PC_PREFIX "%INSTALL_PREFIX%")
set(PC_LIBDIR "\${exec_prefix}/${CMAKE_INSTALL_LIBDIR}")
set(PC_INCLUDEDIR "\${prefix}/${CMAKE_INSTALL_INCLUDEDIR}")
configure_file(cmake/mylib.pc.in mylib.pc.configured @ONLY)
install(CODE "file(READ \"${CMAKE_CURRENT_BINARY_DIR}/mylib.pc.configured\" pc)
string(REPLACE %INSTALL_PREFIX% \"\${CMAKE_INSTALL_PREFIX}\" pc \"\${pc}\")
file(WRITE \"${CMAKE_CURRENT_BINARY_DIR}/mylib.pc\" \"\${pc}\")")
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/mylib.pc
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig
)
```

jm's root template differs from this sample in two ways. It installs the
whole header tree, `install(DIRECTORY native/inc/ ...)`, with a
`PATTERN "pyex_common.h" EXCLUDE` so the Python-only header stays behind. And
it tags every rule with an install component (gh-1601): the shared library a
program loads is `COMPONENT runtime`; everything a build needs -- headers, the
static library, the unversioned `lib<pkg>.so` link (`NAMELINK_COMPONENT dev`),
the CMake package and the `.pc` -- is `dev`. `cmake --install --component`
with `runtime` or `dev` installs one half, which is what a distribution's
split packages are built from; a plain `cmake --install` installs both.

### Version Compatibility Modes

`write_basic_package_version_file` takes a `COMPATIBILITY` argument:

| Mode               | Meaning                                                  |
| ------------------ | -------------------------------------------------------- |
| `SameMajorVersion` | 1.x satisfies requests for 1.y (safe default for semver) |
| `SameMinorVersion` | 1.2.x satisfies 1.2.y (stricter, use for unstable APIs)  |
| `AnyNewerVersion`  | 2.0 satisfies a request for 1.0 (dangerous, avoid)       |
| `ExactVersion`     | Only exact match (too strict for most use)               |

`SameMajorVersion` is the right default for a semver library at 1.0 or later.
Under `0.x` semver lets any minor release break, so there it is
`SameMinorVersion`. jm's root template picks by `PROJECT_VERSION_MAJOR` and
sets the shared library's `SOVERSION` by the same rule (`0.y`, then `x`), so
the soname and `find_package` agree about what is compatible (gh-1582).

### NAMESPACE Convention

Always pass `NAMESPACE mylib::` to `install(EXPORT ...)`. This means
consumers write:

```cmake
find_package(mylib REQUIRED)
target_link_libraries(app PRIVATE mylib::mylib)
```

The double-colon makes it clear the target is an imported CMake target
(not a raw library name), and prevents name collisions across packages.

______________________________________________________________________

## What Gets Installed Where

A jm project `<pkg>` with one object `<comp>`, after
`cmake --install build --prefix <prefix>` (on a configure whose
`CMAKE_INSTALL_LIBDIR` is `lib`; see the Debian note below):

```
<prefix>/
├── include/<pkg>/
│   ├── <pkg>.h                         # the umbrella header
│   ├── clib_common.h
│   └── <comp>/<comp>_core.h
└── lib/
    ├── lib<pkg>.so -> lib<pkg>.so.0.1  # the namelink (dev)
    ├── lib<pkg>.so.0.1 -> lib<pkg>.so.0.1.0
    ├── lib<pkg>.so.0.1.0
    ├── lib<pkg>.a
    ├── cmake/<pkg>/
    │   ├── <pkg>-config.cmake
    │   ├── <pkg>-config-version.cmake
    │   ├── <pkg>-targets.cmake
    │   └── <pkg>-targets-release.cmake
    └── pkgconfig/
        └── <pkg>.pc                    # Libs: -L${libdir} -l<pkg> -lm
```

The `0.1` in the library's names is the soname rule above, for a project at
version `0.1.0`.

______________________________________________________________________

## Verifying the Install

Always write a post-install smoke test. Minimum checks:

```bash
#!/usr/bin/env bash
set -euo pipefail
PREFIX="${1:-/usr/local}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

# pkg-config
pkg-config --exists mylib
pkg-config --modversion mylib
pkg-config --cflags mylib
pkg-config --libs mylib

# headers and library files
test -f "${PREFIX}/include/mylib.h"
LIBDIR=$(pkg-config --variable=libdir mylib)   # lib, lib64 or lib/<multiarch>
ls "${LIBDIR}/libmylib"*

# CMake config files
test -f "${PREFIX}/lib/cmake/mylib/mylibConfig.cmake"
test -f "${PREFIX}/lib/cmake/mylib/mylibTargets.cmake"

# compile smoke test
cat > /tmp/smoke.c <<'EOF'
#include <mylib.h>
int main(void) { return 0; }
EOF
gcc -o /tmp/smoke /tmp/smoke.c \
    $(pkg-config --cflags --libs mylib) \
    "-Wl,-rpath,${LIBDIR}"
/tmp/smoke   # with no LD_LIBRARY_PATH: that would hide a missing rpath
```

Run it as `bash test_install.sh "$HOME/.local"` for non-root installs.

______________________________________________________________________

## Platform Notes

### Non-system Prefix (any platform)

pkg-config only scans standard system paths by default. For any prefix
other than `/usr` or `/usr/local`:

```sh
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH"
```

For CMake:

```sh
cmake -DCMAKE_PREFIX_PATH="$HOME/.local" ..
```

Or at install/configure time, pass `--prefix` or `-DCMAKE_INSTALL_PREFIX`.

Those two find the package; neither makes a program LOAD its shared library.
A program needs an rpath to the libraries it links (and each installed library
needs one to the libraries it links: jm installs its libraries with RUNPATH
`$ORIGIN` plus the directory of any dependency elsewhere). What a consumer
writes, per platform and prefix, is in
[C library — Runtime loading](../c-library.md#runtime-loading-rpath).

### Debian / Ubuntu (multiarch)

`GNUInstallDirs` sets `CMAKE_INSTALL_LIBDIR` to `lib/<multiarch>` (e.g.
`lib/x86_64-linux-gnu`) on Debian/Ubuntu when `CMAKE_INSTALL_PREFIX` is `/usr`
at configure time, and to `lib` otherwise. The value is fixed at configure
time, so `cmake --install --prefix /usr` does not change it: pass the prefix
when configuring if you want the multiarch dir. pkg-config scans that path
automatically. Do not hardcode `lib`; always use `${CMAKE_INSTALL_LIBDIR}`.

The smoke test above should probe both paths:

```sh
PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib/$(gcc -dumpmachine)/pkgconfig"
```

### macOS (Homebrew)

- Intel: prefix `/usr/local`, Apple Silicon: `/opt/homebrew`
- No multiarch — `CMAKE_INSTALL_LIBDIR` is just `lib`
- A jm library's install name is its absolute path (gh-1594), so a program
    loads it from any prefix with no rpath and no `DYLD_LIBRARY_PATH`
- CMake's `find_package` searches `$(brew --prefix)/lib/cmake` automatically

______________________________________________________________________

## Quick Checklist

- [ ] `include(GNUInstallDirs)` and `include(CMakePackageConfigHelpers)` at top
- [ ] `$<BUILD_INTERFACE:...>` / `$<INSTALL_INTERFACE:...>` on include paths
- [ ] `install(TARGETS ... EXPORT ...)` with `LIBRARY`, `ARCHIVE`, `RUNTIME`
- [ ] `install(EXPORT ... NAMESPACE mylib:: ...)` for the Targets file
- [ ] `configure_package_config_file` (not `configure_file`) for the Config
- [ ] `write_basic_package_version_file`: `SameMinorVersion` under 0.x,
    `SameMajorVersion` from 1.0
- [ ] `VERSION` / `SOVERSION` on the shared library, by the same rule
- [ ] `export(EXPORT ...)` so the build tree is a package too
- [ ] `.pc` prefix: absolute, written at install time (honours
    `--prefix`, not `DESTDIR`); libdir/includedir follow it unless they
    were given as absolute paths
- [ ] Private deps in `Requires.private` / `Libs.private`, not `Requires`
- [ ] Post-install smoke test that covers both pkg-config and CMake consumers
- [ ] Document the `PKG_CONFIG_PATH` and `CMAKE_PREFIX_PATH` overrides for
    non-system installs
