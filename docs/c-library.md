# Installing your C library for end users

Your generated project is already a distributable C library. After
`cmake --install`, end users who don't touch Python at all can link against
it with a single `pkg-config` or `find_package` call:

```sh
gcc $(pkg-config --cflags my_project) consumer.c \
    $(pkg-config --libs my_project) -o consumer
```

```cmake
find_package(my_project REQUIRED)
target_link_libraries(my_app PRIVATE my_project::my_project)
```

No just-makeit required on the consumer's machine. The sections below walk
through prerequisites, build, install, and runtime loading.

______________________________________________________________________

## Prerequisites

The end user needs the following tools — **just-makeit itself is not required**:

| Tool           | Minimum version  | Notes                                                                              |
| -------------- | ---------------- | ---------------------------------------------------------------------------------- |
| CMake          | 3.16             | Build system; drives configure + install                                           |
| A C99 compiler | GCC 8 / Clang 10 | `gcc` or `clang`; on Windows `clang-cl` — MSVC's `cl.exe` rejects `float _Complex` |
| pkg-config     | any              | For pkg-config consumers only                                                      |

**Linux (Debian/Ubuntu):**

```sh
sudo apt-get install cmake gcc pkg-config
```

**macOS (Homebrew):**

```sh
brew install cmake pkg-config
# gcc ships with Xcode Command Line Tools: xcode-select --install
```

**Windows:** Visual Studio Build Tools (C++ workload) plus LLVM for
`clang-cl` — see [Does it work on Windows?](faq.md#does-it-work-on-windows).

______________________________________________________________________

## Overview

Every just-makeit project ships a `lib<project>.so` in addition to its Python
extensions. The same C code drives both: each component compiles once as a
CMake OBJECT library and links into both the Python `.so` and the combined
shared library. End users who don't use Python at all can consume it via the
standard mechanisms below.

This applies to all project layouts — standalone objects (`just-makeit object`),
multi-type modules (`just-makeit module` + `just-makeit object --module`), or a mix of both.

______________________________________________________________________

## What gets installed

```
$PREFIX/
├── include/
│   └── my_project/              # every header, under the package name
│       ├── my_project.h         # umbrella: #include <my_project/my_project.h>
│       ├── clib_common.h
│       ├── component_a/
│       │   └── component_a_core.h
│       └── component_b/
│           └── component_b_core.h
├── lib/
│   ├── libmy_project.so.0.1.0   # shared library (the real file)
│   ├── libmy_project.so.0.1     # -> .so.0.1.0, its soname
│   ├── libmy_project.so         # -> .so.0.1, what `-lmy_project` finds
│   ├── libmy_project.a          # static library
│   ├── pkgconfig/
│   │   └── my_project.pc        # pkg-config descriptor
│   └── cmake/my_project/
│       ├── my_project-config.cmake
│       ├── my_project-config-version.cmake
│       └── my_project-targets*.cmake
```

Every file belongs to one of two install components, the split a
distribution packages from (`lib<name>` and `lib<name>-dev`):

| component | installs                                                                                                               |
| --------- | ---------------------------------------------------------------------------------------------------------------------- |
| `runtime` | the shared library a program loads: `libmy_project.so.0.1.0` and its soname link (a `.dll` on Windows)                 |
| `dev`     | everything a build needs: the headers, `libmy_project.a`, the `libmy_project.so` link, the CMake package and the `.pc` |

```sh
cmake --install build --component runtime --prefix stage/runtime
cmake --install build --component dev --prefix stage/dev
```

A plain `cmake --install` installs both. Tag your own install rules below
`# ── End install` the same way if you package them.

______________________________________________________________________

## Build and install

```sh
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build build
cmake --install build
```

For a non-root local install substitute any writable path, at configure time
or at install time; both work:

```sh
cmake --install build --prefix "$HOME/.local"
```

The `.pc` names the prefix its files were **installed** under, as an absolute
path, written at install time. So `cmake --install --prefix` is honoured, and
under `/usr` pkg-config can leave out `-I/usr/include` and `-L/usr/lib...` as
it does for every system package. A `CMAKE_INSTALL_LIBDIR` given as an
absolute path, as Nix and Guix do, is written as that path.

Relocating an installed tree is the **consumer's** side of pkg-config:

| the tree was...        | the consumer runs                               |
| ---------------------- | ----------------------------------------------- |
| staged with `DESTDIR`  | `PKG_CONFIG_SYSROOT_DIR=<stage> pkg-config ...` |
| moved after installing | `pkg-config --define-prefix ...`                |

A staged `.pc` names the real target, not the staging directory, which is
what a distribution package needs. `--define-prefix` derives the prefix from
where the `.pc` now sits, which is right for `lib/pkgconfig`. It is wrong for
a multiarch `lib/<triplet>/pkgconfig`, where pkg-config takes `lib` as the
prefix: that is a pkg-config limitation, so reinstall instead. The CMake
config needs neither, since it locates itself.

### Versions and ABI

The shared library carries a versioned soname, so a release that changes the
ABI installs beside the old one instead of over it. `find_package` applies
the same rule when a consumer asks for a version:

| project version | soname                 | `find_package(my_project X.Y)` accepts |
| --------------- | ---------------------- | -------------------------------------- |
| `0.y.z`         | `libmy_project.so.0.y` | `0.y.*` at or above the request        |
| `x.y.z`, x ≥ 1  | `libmy_project.so.x`   | `x.*` at or above the request          |

Under `0.x` a minor release may break compatibility (semver), which is why
`0.1` does not accept `0.2`. The version is `project(... VERSION)` in the root
`CMakeLists.txt`. The `.pc`'s `Description:` and `URL:` come from that same
`project()` call's `DESCRIPTION` and `HOMEPAGE_URL`.

______________________________________________________________________

## Using with pkg-config

```sh
pkg-config --cflags --libs my_project   # verify it resolves
```

Compile a consumer:

```sh
gcc $(pkg-config --cflags my_project) \
    consumer.c \
    $(pkg-config --libs my_project) \
    -o consumer
```

The `.pc` names everything the library needs, libm included, so the
consumer adds nothing by hand. To link statically, pass the archive in place
of `-lmy_project` (a bare `-l` finds the shared library first) and let
`--static` add what the archive needs:

```sh
gcc $(pkg-config --cflags my_project) consumer.c \
    /usr/local/lib/libmy_project.a \
    $(pkg-config --static --libs my_project | sed 's/-lmy_project//') \
    -o consumer
```

> **Linux / `--as-needed` note:** Split `--cflags` and `--libs` with the
> source file between them. GNU ld on Debian/Ubuntu uses `--as-needed` by
> default, which silently drops any shared library that appears *before* the
> object files referencing it. If you merge them with the source last
> (`$(pkg-config --cflags --libs my_project) consumer.c`) you will get
> undefined-reference errors at link time even though the library is present.

If you installed to a non-standard prefix, point pkg-config at it:

```sh
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig"
```

______________________________________________________________________

## Using with CMake

```cmake
cmake_minimum_required(VERSION 3.16)
project(my_consumer C)

find_package(my_project REQUIRED)

add_executable(consumer consumer.c)
target_link_libraries(consumer PRIVATE my_project::my_project)
```

`my_project::my_project` is the shared library and
`my_project::my_project-static` the static one. Each carries its own link
interface, libm included, so the consumer names nothing else.

Configure with the prefix if it's not on the default search path:

```sh
cmake -B build -DCMAKE_PREFIX_PATH="$HOME/.local"
cmake --build build
```

A consumer can also build against the library's **build directory** with
nothing installed, because the build tree exports the same targets:

```sh
cmake -B build -Dmy_project_DIR=/path/to/my_project/build
```

______________________________________________________________________

## When your library depends on another package

A component that calls into another C library links it with
`extra_link_libs`, and that package is declared once, on `[project]`. Declare
it with the name each consumer face needs:

```toml
[project]
find_packages = [
    { name = "Doppler", pkg_config = "doppler" },   # ships a CMake config and a .pc
    { name = "Threads", libs_private = "-pthread" }, # no .pc of its own
    { name = "Kiss", cflags = "-I/opt/kiss/include", libs_private = "-L/opt/kiss/lib -lkiss" },
]
pkg_modules = ["fftw3f", "zlib >= 1.2"]              # found through pkg-config

[tone]
extra_link_libs = ["doppler::doppler-static"]
```

- **`name`** is the CMake package. The root calls `find_package(Doppler)`,
    and the installed `my_project-config.cmake` calls `find_dependency(Doppler)`.
- **`pkg_config`** is the dependency's pkg-config module. The installed `.pc`
    lists it as `Requires.private`. A CMake package name says nothing about
    its module name (`PNG` is `libpng`, `CURL` is `libcurl`), so this is the
    one fact you state rather than jm derives.
- **`libs_private`** is for a dependency that ships no `.pc`. Its flags go to
    the `.pc`'s `Libs.private`, pkg-config's field for exactly that case.
- **`cflags`** is the compile half of the same case. When one of your headers
    includes a header of a dependency with no `.pc`, a consumer needs its
    include flags to compile yours. pc(5) has no private Cflags, because a
    header's includes are needed however the consumer links, so these go on
    the `.pc`'s own `Cflags` line.
- **`pkg_modules`** entries are already pkg-config module names, so they reach
    `Requires.private` with nothing more to say. An entry may carry a version
    bound the way pc(5) writes one: a name, then one of `=`, `<`, `>`, `<=`,
    `>=` and a version (`"zlib >= 1.2"`). The name alone gives the target
    (`PkgConfig::ZLIB`). The bound reaches `pkg_check_modules` in the root and
    in the installed config, and the `.pc`'s `Requires.private`. `jm apply`
    refuses any other spelling.

With that, a consumer of the installed project gets the dependency on every
face, and names nothing but your project:

| consumer               | compiling your headers                                | linking                                    |
| ---------------------- | ----------------------------------------------------- | ------------------------------------------ |
| `find_package`, shared | the dependency's include dirs and flags               | already resolved inside `libmy_project.so` |
| `find_package`, static | the same                                              | the dependency, through the link interface |
| `pkg-config`, shared   | its `Cflags` via `Requires.private`, or your `cflags` | already resolved inside `libmy_project.so` |
| `pkg-config --static`  | the same                                              | its `Libs`, or your `libs_private`         |

"Compiling your headers" matters whenever a header of yours includes one of
the dependency's, as nco_tone's `tone_core.h` includes doppler's
`nco/nco_core.h`. The consumer still needs the dependency *installed*: put its
prefix on `CMAKE_PREFIX_PATH` or `PKG_CONFIG_PATH` beside yours.

A bare string entry (`find_packages = ["Doppler"]`) still works through
`find_package`, but the `.pc` cannot name it: a pkg-config consumer then gets
no `Cflags` for it, and no libs when linking static. `jm status` lists such
entries under **PKG-CONFIG**, so the gap is never silent.

Link a package's **imported target** (`doppler::doppler-static`,
`PkgConfig::FFTW3F`) rather than a path or a `${VAR}` holding one. Only a
target carries the include dirs and flags a consumer needs, and a path would
put this machine's layout into the installed package.

______________________________________________________________________

## Flags your headers need of every consumer

Some needs have no package to declare. An installed header with an inline
function that calls pthread makes every consumer's own object call it, so
every consumer links `-lpthread`. A header that names `MAP_ANONYMOUS` or calls
`syscall()` compiles under `-std=c99` only if `_GNU_SOURCE` is defined before
libc is first reached, so that definition has to be on every consumer's
compile line:

```toml
[project]
public_link_libs = ["-lpthread"]    # one linker flag per entry
public_defines   = ["_GNU_SOURCE"]  # an identifier, optionally =value; no -D
```

Each reaches every face, from this manifest alone:

| face                     | `public_link_libs`                                                        | `public_defines`                          |
| ------------------------ | ------------------------------------------------------------------------- | ----------------------------------------- |
| this project's own build | its tests, benchmarks and Python extensions link it                       | every target compiles with it             |
| `find_package`           | the exported targets' link interface, shared and static                   | the exported targets' compile definitions |
| pkg-config               | the `.pc`'s `Libs:` -- public, like `-lm`: the consumer's object calls it | the `.pc`'s `Cflags:`, as `-D<def>`       |

An entry of `public_link_libs` is one linker flag (`-lpthread`, `-pthread`,
`-ldl`), the spelling CMake and pkg-config read the same. A package with a
CMake config or a `.pc` of its own (`Threads::Threads`) is a dependency, not a
flag: declare it in `find_packages` above.

______________________________________________________________________

## More than one library

A project installs `lib<pkg>` and, for each `[project.libraries.<name>]`
table, one more library built from the OBJECT libraries it names. It is how a
project ships an optional layer (a network transport, say) that a consumer who
does not need it never links:

```toml
[project.libraries.stream]
cores = ["stream_core_obj"]   # OBJECT libraries, by CMake target name
description = "the stream layer"
platforms = ["linux", "macos"]   # optional; built only there
```

| face       | `lib<pkg>`                            | `[project.libraries.stream]`            |
| ---------- | ------------------------------------- | --------------------------------------- |
| file       | `lib<pkg>.so` / `.a`                  | `lib<pkg>_stream.so` / `.a`             |
| pkg-config | `<pkg>`                               | `<pkg>_stream`, with `Requires: <pkg>`  |
| CMake      | `<pkg>::<pkg>`, `<pkg>::<pkg>-static` | `<pkg>::stream`, `<pkg>::stream-static` |

It links `lib<pkg>` publicly, so a consumer that names only the additional
library gets both, by either route:

```sh
cc app.c $(pkg-config --cflags --libs <pkg>_stream)
```

```cmake
find_package(<pkg> REQUIRED COMPONENTS stream)
target_link_libraries(app PRIVATE <pkg>::stream)
```

Each library gets everything `lib<pkg>` gets -- its soname, its install name,
its runtime/dev install components, its `.pc` -- from the same rules. A library
declared for other platforms is not built, installed or exported where it does
not apply, and `find_package(<pkg> COMPONENTS <name>)` does not find it there.

A core belongs to one library: `apply` refuses one also folded into
`lib<pkg>`, one claimed by two libraries, and one the tree does not declare as
an OBJECT library. jm never folds a claimed core into `lib<pkg>` itself.

## Two packages, one program

Headers under `<pkg>/` keep two installed jm packages' FILES apart, but not
their C symbols: two packages that both have a component `fir` both export
`fir_create`, and a translation unit including both `fir_core.h`s gets only
the first -- the shared `FIR_CORE_H` guard silently drops the second.
`[project] c_prefix` namespaces what jm derives:

```toml
[project]
c_prefix = "dp"      # written without the joining underscore
```

| moves (derived by jm)                                                     | stays (named by you, or not C)                                           |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `fir_create`, `fir_destroy`, `fir_reset`, `fir_step(s)` -> `dp_fir_...`   | a manifest `fn =`, `create_fn`, `bridge_fn`, `type_name`, `record_dtype` |
| a method's `fir_<name>`, a property's `fir_get_<name>` / `fir_set_<name>` | your own macros in the sacred `_core.h` (`ACC_F32_STATE_MAGIC`)          |
| the `fir_state_t` type -> `dp_fir_state_t`                                | the Python names: `Fir`, `from pkg import fir`, a module function `mix`  |
| jm's include guards (`DP_FIR_CORE_H`) and `process_global` defines        | file names: `native/inc/<pkg>/fir/fir_core.h` stays                      |
| a module function `mix` -> `dp_mix` (in C only)                           | `<pkg>_version`                                                          |

A name that already starts with `dp_` is not prefixed again: a component
`dp_tlm` keeps `dp_tlm_create`. So `apply` refuses a project in which two
names derive one symbol -- `x` and `dp_x` both deriving `dp_x_create`, or a
method and a module function that meet -- naming both.

A consumer includes both packages' headers and calls each by its prefix:

```c
#include "pa/fir/fir_core.h"
#include "pb/fir/fir_core.h"

pa_fir_state_t *a = pa_fir_create (2.0);
pb_fir_state_t *b = pb_fir_create (2.0);
```

**A new project has one by default:** `jm new dsp` writes `c_prefix = "dsp"`, so its symbols are `dsp_<comp>_*` from the first file. Pick a
shorter one with `jm new --c-prefix dp`, or keep today's bare names with
`--no-c-prefix`. A manifest you write by hand spells a sibling component's
derived names prefixed too -- `type = "dsp_lfo_state_t *"`,
`create_impl = "obj->osc = dsp_lfo_create(...)"`.

A project made before this default has no key, and keeps its bare names
until you add one. Adding it renames every
derived symbol, which your C (the sacred `_core.h` / `_core.c`, module
function sources, tests and benchmarks) must follow: `apply` refuses, naming
each file and the old names it still spells, until `jm upgrade` respells
them ([upgrading](upgrading.md#symbol-prefix-gh-1591)). It is also an ABI
change for your consumers -- say so in your release notes.

## Calling it from C++11

Every generated header carries an `extern "C"` guard, so a C++ translation
unit can include it and call the C core directly — the mixed case where some
of your algorithms are C99 and some are C++11:

```cpp
#include "<pkg>/engine/engine_core.h"      // the same header your C code includes
#include <vector>

std::vector<float _Complex> xs;      // C99's type, in a C++11 container
<pkg>_engine_state_t *o = <pkg>_engine_create(2.0);
float _Complex y = <pkg>_engine_step(o, xs.at(0));
<pkg>_engine_destroy(o);
```

Compile the C++ side with `-std=c++11` and link with the C++ driver (`g++` /
`clang++`), which is what pulls in the C++ runtime. jm's core stays C: nothing
about your project changes, and jm generates nothing extra.

**The type crosses; C99's complex arithmetic does not.** `I`, `_Complex_I`,
`creal()` and `cimag()` are C-only spellings — in C++ the same `<complex.h>`
gives you `std::complex` instead. Build and inspect values with GNU's
`__real__` / `__imag__`:

```cpp
float _Complex z;
__real__ z = 3.0f;
__imag__ z = 4.0f;
```

jm deliberately does **not** define `I` for C++: a macro named `I` collides
with almost everything.

`_Complex` in C++ is a GNU/Clang extension, so this needs GCC or Clang. MSVC
cannot compile the C99 core either (see Prerequisites), so it is out on both
sides rather than only this one.

Implementing a component *in* C++ is a different thing and jm does not do it —
see gh-1149.

______________________________________________________________________

## Runtime loading (rpath)

**macOS needs nothing.** The installed `.dylib` names itself by its absolute
path (its install name is `<prefix>/lib/lib<pkg>.<ABI>.dylib`, as Homebrew's
libraries are), so a program linked by pkg-config, `find_package`, a Makefile
or Xcode loads it wherever the prefix is. If you move the installed tree
afterwards, point `DYLD_LIBRARY_PATH` at its new `lib/`, or rewrite the name
with `install_name_tool`.

**Linux:** the installed `.so` is not automatically on the dynamic linker's
search path unless you installed to `/usr/local` (or ran `ldconfig` after a
system-wide install).

For a custom prefix, embed the library path in the binary at link time:

**pkg-config:**

```sh
LIB_DIR=$(pkg-config --variable=libdir my_project)
gcc $(pkg-config --cflags my_project) \
    consumer.c \
    $(pkg-config --libs my_project) \
    -Wl,-rpath,"$LIB_DIR" \
    -o consumer
```

**CMake:** set `INSTALL_RPATH_USE_LINK_PATH` or `CMAKE_BUILD_RPATH`:

```cmake
set_target_properties(consumer PROPERTIES INSTALL_RPATH_USE_LINK_PATH ON)
```

Or pass it on the command line:

```sh
cmake -B build \
    -DCMAKE_PREFIX_PATH="$HOME/.local" \
    -DCMAKE_BUILD_RPATH="$HOME/.local/lib"
```

Alternatively, set `LD_LIBRARY_PATH` at runtime (useful for quick testing,
not for deployment):

```sh
LD_LIBRARY_PATH="$HOME/.local/lib" ./consumer
```

______________________________________________________________________

## Verifying the install

```sh
# headers present
ls $PREFIX/include/my_project/my_project.h

# library present and has expected symbols
nm -D $PREFIX/lib/libmy_project.so | grep my_project_component_a_create

# pkg-config resolves
pkg-config --modversion my_project
```
