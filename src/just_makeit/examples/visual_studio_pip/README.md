# visual_studio_pip example

A generated project built and tested the way a Visual Studio user does it on
Windows, with the project's `.venv` made by **pip**: scaffold from
PowerShell, create the venv, open the folder, pick a preset, build, run the
tests. Using uv instead? Follow
[Visual Studio on Windows (uv)](visual_studio_uv.md); the two pages differ
only in the venv step.

Every IDE action below is shown next to the command line it maps to. Visual
Studio and the command line read the same generated `CMakePresets.json`
(gh-1376), so each pair configures, builds and tests the same thing. This
example's own test runs the commands on this page, step by step, and CI runs
them as written on `windows-latest` with clang-cl.

## TL;DR -- see it work first

From a **Developer PowerShell for VS**, which loads the build environment
clang-cl needs:

```powershell
jbx just-makeit:install
jm example visual_studio_pip
# visual_studio_pip: PASSED
```

If `jm` is not found in a new shell, run `jbx update-path` once.

## Prerequisites

Visual Studio 2022 or later with the *Desktop development with C++*
workload, clang-cl, and an x64 Python 3.9+ with the `py` launcher. The full
list, and why the compiler is clang-cl rather than `cl.exe`, is in
[Building on Windows](../windows.md#what-you-need). Visual Studio ships
CMake and Ninja, so nothing else is needed.

jm is installed with [jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit), jm's recommended install,
the same line on every platform; step 1 runs it.

---

## 1. Install jm, then scaffold a project and a component

From a PowerShell prompt, in the folder that will hold the project:

```powershell
jbx just-makeit:install
jm new my_vs
cd my_vs
jm object scale --state factor:double:2.0 --arg-type double --return-type double
```

The first line installs jm, the same in Windows PowerShell and
PowerShell 7: from PowerShell,
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
runs jm's Windows installer,
`install.ps1`, in this window. It installs just-makeit into its own venv,
`%LOCALAPPDATA%\just-makeit\venv`, copies its launchers into
`%USERPROFILE%\.local\bin`, and puts both folders on this window's `PATH`,
so `jm` runs on the next line. It does not activate that venv: the
project's own `.venv` comes in step 3, and an active venv would change what
`py` and `uv` pick. If `jm` is not found in a new window, run
`jbx update-path` once.
[Building on Windows](../windows.md#installing-jm) says more.

`scale` is one object with one piece of state, `factor`, and a `step()` that
takes and returns a `double`. Its C core is
`native\inc\my_vs\scale\scale_core.h`, and the project root holds the
`CMakePresets.json` the next steps use.

---

## 2. Implement `step()`

Open `native\inc\my_vs\scale\scale_core.h` in any editor -- Visual Studio's,
once the folder is open in step 4 -- and replace the generated stub of
`my_vs_scale_step` with:

```c
static inline double
my_vs_scale_step (const my_vs_scale_state_t *state, double x)
{
  return state->factor * x;
}
```

Nothing else changes: the binding, the `.pyi` stub and both test suites are
jm's, and already call it.

---

## 3. Create the project's `.venv` -- before opening Visual Studio

The presets name the project's own interpreter, `.venv\Scripts\python.exe`,
so it must exist, with numpy, before the first configure. From the same
prompt, in `my_vs`, with no virtual environment active:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install numpy
```

The first line makes the venv, with pip in it; the second installs numpy
into it. [Building on Windows](../windows.md#the-projects-venv) explains why
the presets name an interpreter rather than search for one.

Only on a project with no `.venv` yet. A `.venv` that uv made -- `uv sync`
makes one -- has no pip, and running `py -m venv .venv` over it does not add
one: keep it, and follow the [uv page](visual_studio_uv.md) instead.

---

## 4. Open the folder and pick a preset

| Visual Studio                                 | Command line                     |
| --------------------------------------------- | -------------------------------- |
| **File > Open > Folder**, and pick `my_vs`    | `cd my_vs`                       |
| Toolbar preset list: **Release (clang-cl)**   | `cmake --preset windows-release` |

Visual Studio reads `CMakePresets.json`, loads the Microsoft build environment
itself, and configures as soon as the preset is chosen. The same configure
from a **Developer PowerShell for VS**, which loads that environment:

```powershell
cmake --preset windows-release
```

Open the folder only once step 3 has made `.venv`. Opened first, the
configure stops with `Python3_EXECUTABLE is .../.venv/Scripts/python.exe,
which does not exist`, and Visual Studio keeps that failed configure: create
the `.venv` as in step 3, then **Project > Delete Cache and Reconfigure**.

The preset alone chooses clang-cl, Ninja and the `.venv` interpreter; nothing
in the environment has to. The build tree is `out\build\windows-release\`.
**Debug (clang-cl)** and **RelWithDebInfo (clang-cl)** are the same with
`windows-debug` and `windows-relwithdebinfo`.

---

## 5. Build

| Visual Studio         | Command line                             |
| --------------------- | ---------------------------------------- |
| **Build > Build All** | `cmake --build --preset windows-release` |

```powershell
cmake --build --preset windows-release
```

The build compiles the C core, its CTest executables and the Python
extension, and writes the extension (`.pyd`) into `src\my_vs\`, next to its
`.pyi`.

---

## 6. Run the tests

| Visual Studio                              | Command line                     |
| ------------------------------------------ | -------------------------------- |
| **Test > Test Explorer**, then **Run All** | `ctest --preset windows-release` |
| **View > Terminal**, then the line below   | the Python suite                 |

Test Explorer lists the CTest tests the build registered. The same run:

```powershell
ctest --preset windows-release
```

The Python suite imports the `.pyd` from `src\`, through the `.venv`
interpreter:

```powershell
.venv\Scripts\python -m unittest discover -s src -p "test_*.py" -v
```

The generated tests run under `unittest` as written; with pytest installed in
the `.venv`, `.venv\Scripts\python -m pytest src` runs them too.

If the configure step fails with `Could NOT find Python3` and names
`NumPy`, the `.venv` has no numpy: run the last line of step 3 again, then
**Project > Delete Cache and Reconfigure**.

---

## See also

- [Building on Windows](../windows.md) -- the prerequisites, the presets,
    `CMakeUserPresets.json` for your own, and line endings
- [Build & Run](../commands/build.md) -- `jm build` and `jm test`, the same
    project through the generated `Makefile`
