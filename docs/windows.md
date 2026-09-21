# Building on Windows

A generated project builds, tests and imports on Windows exactly as generated,
with **clang-cl** and no flag or manifest key (gh-1368). CI builds every
bundled example with clang-cl on `windows-latest` on every pull request, and
that job gates the merge.

There are two ways in: **PowerShell**, from a terminal, and the **Visual Studio
IDE**, opening the project folder. Both configure through the same generated
`CMakePresets.json` (gh-1376), so they build the same thing.

## What you need

- **Visual Studio 2022 or later**, or the standalone **Build Tools**, with the
    *Desktop development with C++* workload: the MSVC libraries and Windows
    SDK that clang-cl links against. CMake and Ninja ship with it.
- **clang-cl**: Visual Studio's *C++ Clang Compiler for Windows* component,
    or LLVM (`winget install LLVM.LLVM`).
- **Python 3.9+**, x64.

**Why clang-cl and not MSVC's `cl.exe`:** jm generates C99 `float _Complex`,
which `cl.exe` does not have at all; `clib_common.h` stops such a build with
an `#error` naming the fix. clang-cl has `_Complex` while targeting the same
MSVC ABI that Python itself is built with.

## The project's `.venv`

Both ways find Python the same way: the presets name the project's own
virtual environment, `.venv\Scripts\python.exe`, rather than letting CMake
search for an interpreter. A search on a machine with more than one Python
can configure against one numpy and import against another (gh-814), so
the venv has to exist before the first configure. From the project folder:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install numpy
```

or, with uv, `uv venv` then `uv pip install numpy`.

## PowerShell

Open a **Developer PowerShell for VS**, so the MSVC environment clang-cl needs
is loaded, and `cd` into the project:

```powershell
cmake --preset windows-release
cmake --build --preset windows-release
ctest --preset windows-release
.venv\Scripts\python -m unittest discover -s src -p "test_*.py" -v
```

The build writes the extension (`.pyd`) into `src\<package>\`, next to its
`.pyi`, so the last line imports it from there. `cmake --list-presets` shows
the rest: `windows-debug` and `windows-relwithdebinfo`. Preset builds go to
`out\build\<preset>\`, which the generated `.gitignore` ignores.

**With GNU make installed**, `make build` and `make test` work too. The
generated `Makefile` selects clang-cl and Ninja on Windows by itself, and
`CC` or `CMAKE_GENERATOR` override either. It builds into `build\`, so it
never shares a CMake cache with the presets.

## Visual Studio

1. **File > Open > Folder**, and pick the project folder. Visual Studio reads
    `CMakePresets.json` and loads the Microsoft environment itself; no
    Developer PowerShell is needed.
1. Pick a configuration in the toolbar's preset list: **Release (clang-cl)**,
    **Debug (clang-cl)** or **RelWithDebInfo (clang-cl)**. The Linux and macOS
    presets are hidden on Windows.
1. **Build > Build All**.
1. **Test > Test Explorer** lists the CTest tests; **Run All** runs them.

If the configure step fails with `Could NOT find Python3`, the `.venv` is
missing or has no numpy; create it as above, then **Project > Delete Cache
and Reconfigure**.

VS Code's *CMake Tools* extension reads the same file, on every platform.

## Changing the presets

`CMakePresets.json` is jm's: `jm status` reports it outdated when a newer jm
renders it differently, the same as the `Makefile` and `.gitignore`. Your own
presets go in **`CMakeUserPresets.json`** beside it. CMake and Visual Studio
read both, the user file may `inherit` from jm's presets, and it is
gitignored, so a different interpreter path, extra cache variables or a
personal build directory stay on your machine:

```json
{
  "version": 3,
  "configurePresets": [
    {
      "name": "my-release",
      "inherits": "windows-release",
      "cacheVariables": {
        "Python3_EXECUTABLE": "C:/Python312/python.exe"
      }
    }
  ]
}
```

A project created before the file existed gets it from `jm apply`, which adds
missing files and changes nothing else.

## Not supported

- **MinGW** (gcc on Windows), retired in 0.77.0. A `"windows"` entry in
    `platforms`, and `jm new --windows`, now only print a notice; `jm apply`
    removes the MinGW blocks they used to emit.
- **The `make` build backend** (`build = "make"`) is POSIX-only, and gets no
    presets; use the CMake backend on Windows.
- **Windows on ARM64 natively.** The MSVC ARM64 libraries are a separate
    Build Tools component, and CI covers x64 only.

## Behaviour that differs from Linux

Complex multiply and divide are built with `-fcx-limited-range`, so they skip
C99 Annex G's inf/NaN corner cases (the alternative is an MSVC link that
cannot resolve `__mulsc3`). Python extensions always use the release C
runtime (`/MD`), including in Debug builds, so they link against the ordinary
`python3X.lib`.
