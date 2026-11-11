# Building on Windows

A generated project builds, tests and imports on Windows exactly as generated,
with **clang-cl** and no flag or manifest key (gh-1368). CI builds every
bundled example with clang-cl on `windows-latest` on every pull request that
changes code, and that job gates the merge.

There are two ways in: **PowerShell**, from a terminal, and the **Visual Studio
IDE**, opening the project folder. Both configure through the same generated
`CMakePresets.json` (gh-1376), so they build the same thing.

## Installing jm

From PowerShell -- Windows PowerShell 5.1 or PowerShell 7 -- with a
Python 3.9+ installed, the recommended way is jbx, the same line as on
Linux and macOS ([getting jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)):

```powershell
jbx just-makeit:install
```

From PowerShell, jbx runs jm's Windows installer, `install.ps1`, in the window
itself. Without jbx, run that installer directly:

```powershell
irm https://just-buildit.github.io/just-makeit/install.ps1 | iex
```

Either way it installs just-makeit into a venv of its own,
`%LOCALAPPDATA%\just-makeit\venv`, and puts that venv's `Scripts` folder first
on this window's `PATH`, so `jm` works at once. Then it reports whether
clang-cl and cmake are on `PATH`, which a build needs: a warning, not a
failure, since jm itself runs without them, and a Developer PowerShell for VS
has both. It finds Python through the `py` launcher, then `python` on `PATH`,
then `uv python find`.

The venv is put on `PATH`, never activated. A project makes its own `.venv`
(below), and an activated venv sets `VIRTUAL_ENV`, which makes `py` run that
venv's Python and makes uv warn that it is not the project's. Nothing outside
the window changes either: no profile and no user `PATH` is edited, as with
the Linux and macOS installer. In a new window, run the same line again; when
jm is current it downloads nothing and only sets `PATH`.

Options are environment variables, since `irm | iex` passes no arguments:
`$env:JM_VENV` picks the venv, `$env:JM_FORCE = '1'` rebuilds it, and
`$env:JM_INSTALL_FROM` installs a wheel instead of PyPI's release.

`pip install just-makeit` alone is not enough on Windows: pip puts `jm.exe`
in a Python `Scripts` folder that is usually not on `PATH`, so `jm` is not
found. From Git Bash, `install.sh` stops and names this line.

## What you need

- **Visual Studio 2022 or later**, or the standalone **Build Tools**, with the
    *Desktop development with C++* workload: the MSVC libraries and Windows
    SDK that clang-cl links against. CMake and Ninja ship with it.
- **clang-cl**: Visual Studio's *C++ Clang Compiler for Windows* component,
    or LLVM (`winget install LLVM.LLVM`).
- **Python 3.9+**, x64.

The first two are declared in the project's `bootstrap.toml`, in its
`[dev.winget]` table, so `make install-deps` from Git Bash installs them
(gh-2598). It asks `vswhere` for a Visual Studio instance that has both. If
none does, it installs the Build Tools with them, or adds them to Build Tools
already installed, raising a UAC prompt when the shell is not elevated.

**Why clang-cl and not MSVC's `cl.exe`:** jm generates C99 `float _Complex`,
which `cl.exe` does not have at all; `clib_common.h` stops such a build with
an `#error` naming the fix. clang-cl has `_Complex` while targeting the same
MSVC ABI that Python itself is built with.

## The project's `.venv`

Both ways find Python the same way: the presets name the project's own
virtual environment, `.venv\Scripts\python.exe`, rather than letting CMake
search for an interpreter. A search on a machine with more than one Python
can configure against one numpy and import against another (gh-814), so
the venv has to exist before the first configure: make it before opening
the folder in Visual Studio. From the project folder, with no virtual
environment active, either with pip:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install numpy
```

or with uv, which creates `.venv` from `pyproject.toml` with numpy and the
project itself (editable) installed, and writes `uv.lock`:

```powershell
uv sync
```

A venv uv makes has no pip, by design: add packages to it with `uv add` or
`uv pip install`, not `python -m pip`. Do not run `py -m venv .venv` over an
existing `.venv` either; that does not add pip to it.

When `.venv` is missing, the configure stops with `Python3_EXECUTABLE is .../.venv/Scripts/python.exe, which does not exist`, giving both ways to
create it (gh-2610). The generated `Makefile` uses the same `.venv` when it
exists, so `make` and the presets build against one interpreter.

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
`CC` or `CMAKE_GENERATOR` override either. It builds against the project's
`.venv` when there is one, the first `python` on `PATH` otherwise, and
`PYTHON=` overrides it. It builds into `build\`, so it never shares a CMake
cache with the presets.

## Visual Studio

1. **File > Open > Folder**, and pick the project folder. Visual Studio reads
    `CMakePresets.json` and loads the Microsoft environment itself; no
    Developer PowerShell is needed.
1. Pick a configuration in the toolbar's preset list: **Release (clang-cl)**,
    **Debug (clang-cl)** or **RelWithDebInfo (clang-cl)**. The Linux and macOS
    presets are hidden on Windows.
1. **Build > Build All**.
1. **Test > Test Explorer** lists the CTest tests; **Run All** runs them.

If the folder was opened before `.venv` existed, the configure stops
saying so, and Visual Studio keeps that failed configure: create the `.venv`
as above, then **Project > Delete Cache and Reconfigure**. The same applies
to `Could NOT find Python3` naming `NumPy`: the `.venv` has no numpy.

VS Code's *CMake Tools* extension reads the same file, on every platform.

Two examples walk through all of it, from `jm new` to a passing test run,
with each IDE action next to the command it maps to, one for each way of
making the `.venv`: [Visual Studio with pip](examples/visual_studio_pip.md)
and [Visual Studio with uv](examples/visual_studio_uv.md). CI's clang-cl
job runs both pages' commands as written.

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

## Line endings

jm writes every file with LF line endings, on Windows too (gh-1368), and
`jm status` compares the tree against that render. Git for Windows checks text
files out CRLF by default (`core.autocrlf=true`), so `jm new` writes a
**`.gitattributes`** that keeps every checkout LF:

```text
* text=auto eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf
```

A project created before the file existed gets it from `jm apply`, like any
other file it adds; until then `jm status --check` lists it as `MISSING`. It
is jm's content and create-only, as `.gitignore` is: add your own attributes
to it, and `jm status` reports it `OUTDATED` when a newer jm renders it
differently. Adding it to a repository that already holds CRLF files changes
nothing on disk until git renormalises them, which you do once, on any
platform:

```powershell
git add --renormalize .
git commit -m "Normalise line endings to LF"
```

A CRLF file whose content is current is not drift (gh-1641): `jm status` lists
it under `LINE ENDINGS`, uncounted, rather than as `STALE` or `OUTDATED`, so
`jm status --check` passes on a Windows checkout either way. A real change
under a CRLF checkout is still `STALE`, and `--diff` shows that change alone.
`jm apply` rewrites the files it regenerates as LF and lists each as `eol`.

## A module that cannot build on Windows

A module over a core that only exists on Linux and macOS (a POSIX-only
library you guard with `if(NOT WIN32)` yourself) declares it:

```toml
[module.stream_sink]
platforms = ["linux", "macos"]
```

On Windows its extension is not built, and its names are absent from the
package that re-exports them, while everything else imports normally. See
[A module built on some platforms only](configuration.md#a-module-built-on-some-platforms-only).

## Not supported

- **MinGW** (gcc on Windows), retired in 0.77.0. A `"windows"` entry in
    `[project] platforms`, and `jm new --windows`, now only print a notice;
    `jm apply` removes the MinGW blocks they used to emit. (That is not the
    per-module key above.)
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
