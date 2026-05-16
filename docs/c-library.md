# Installing your C library for end users

Your generated project is already a distributable C library.  After
`cmake --install`, end users who don't touch Python at all can link against
it with a single `pkg-config` or `find_package` call:

```sh
gcc $(pkg-config --cflags my-project) consumer.c \
    $(pkg-config --libs my-project) -lm -o consumer
```

```cmake
find_package(my_project REQUIRED)
target_link_libraries(my_app PRIVATE my_project::my_project_lib m)
```

No just-makeit required on the consumer's machine.  The sections below walk
through prerequisites, build, install, and runtime loading.

______________________________________________________________________

## Prerequisites

The end user needs the following tools — **just-makeit itself is not required**:

| Tool           | Minimum version              | Notes                                    |
| -------------- | ---------------------------- | ---------------------------------------- |
| CMake          | 3.16                         | Build system; drives configure + install |
| A C99 compiler | GCC 8 / Clang 10 / MSVC 2019 | `gcc` or `clang` on Linux/macOS          |
| pkg-config     | any                          | For pkg-config consumers only            |

**Linux (Debian/Ubuntu):**

```sh
sudo apt-get install cmake gcc pkg-config
```

**macOS (Homebrew):**

```sh
brew install cmake pkg-config
# gcc ships with Xcode Command Line Tools: xcode-select --install
```

**Windows (MSYS2/MinGW):**

```sh
pacman -S mingw-w64-x86_64-cmake mingw-w64-x86_64-gcc mingw-w64-x86_64-pkg-config
```

______________________________________________________________________

## Overview

Every just-makeit project ships a `lib<project>.so` in addition to its Python
extensions.  The same C code drives both: each component compiles once as a
CMake OBJECT library and links into both the Python `.so` and the combined
shared library.  End users who don't use Python at all can consume it via the
standard mechanisms below.

This applies to all project layouts — standalone objects (`just-makeit object`),
multi-type modules (`just-makeit module` + `just-makeit object --module`), or a mix of both.

______________________________________________________________________

## What gets installed

```
$PREFIX/
├── include/
│   ├── my_project.h             # umbrella header — include this
│   ├── component_a/
│   │   └── component_a_core.h
│   └── component_b/
│       └── component_b_core.h
├── lib/
│   ├── libmy_project.so         # shared library
│   ├── pkgconfig/
│   │   └── my-project.pc        # pkg-config descriptor
│   └── cmake/my_project/
│       ├── my_project-config.cmake
│       └── my_project-config-version.cmake
```

______________________________________________________________________

## Build and install

Set the install prefix **before** building — cmake bakes the prefix into the
generated `my-project.pc` at configure time.

```sh
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build build
cmake --install build
```

For a non-root local install substitute any writable path:

```sh
cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$HOME/.local"
cmake --install build
```

> **Note:** `make && make test` calls `cmake -B build` internally with the
> default prefix.  If you ran `make` first, re-run the `cmake -S .` line above
> before installing to regenerate the `.pc` file with the correct prefix.

______________________________________________________________________

## Using with pkg-config

```sh
pkg-config --cflags --libs my-project   # verify it resolves
```

Compile a consumer:

```sh
gcc $(pkg-config --cflags my-project) \
    consumer.c \
    $(pkg-config --libs my-project) \
    -lm -o consumer
```

> **Linux / `--as-needed` note:** Split `--cflags` and `--libs` with the
> source file between them.  GNU ld on Debian/Ubuntu uses `--as-needed` by
> default, which silently drops any shared library that appears *before* the
> object files referencing it.  If you merge them with the source last
> (`$(pkg-config --cflags --libs my-project) consumer.c`) you will get
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
target_link_libraries(consumer PRIVATE my_project::my_project_lib m)
```

Configure with the prefix if it's not on the default search path:

```sh
cmake -B build -DCMAKE_PREFIX_PATH="$HOME/.local"
cmake --build build
```

______________________________________________________________________

## Runtime loading (rpath)

The installed `.so` is not automatically on the dynamic linker's search path
unless you installed to `/usr/local` (or ran `ldconfig` after a system-wide
install).

For a custom prefix, embed the library path in the binary at link time:

**pkg-config:**

```sh
LIB_DIR=$(pkg-config --variable=libdir my-project)
gcc $(pkg-config --cflags my-project) \
    consumer.c \
    $(pkg-config --libs my-project) \
    -Wl,-rpath,"$LIB_DIR" \
    -lm -o consumer
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
ls $PREFIX/include/my_project.h

# library present and has expected symbols
nm -D $PREFIX/lib/libmy_project.so | grep component_a_create

# pkg-config resolves
pkg-config --modversion my-project
```
