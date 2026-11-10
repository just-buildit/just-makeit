# visual_studio example

A generated project built and tested the way a Visual Studio user does it on
Windows: scaffold from PowerShell, open the folder, pick a preset, build, run
the tests.

Every IDE action below is shown next to the command line it maps to. Visual
Studio and the command line read the same generated `CMakePresets.json`
(gh-1376), so each pair configures, builds and tests the same thing. This
example's own test runs the commands on this page, step by step, and CI runs
it on `windows-latest` with clang-cl.

## TL;DR -- see it work first

From a **Developer PowerShell for VS**, which loads the build environment
clang-cl needs:

```powershell
py -m pip install just-makeit
jm example visual_studio
# visual_studio: PASSED
```

## Prerequisites

Visual Studio 2022 or later with the *Desktop development with C++*
workload, clang-cl, and an x64 Python 3.9+. The full list, and why the
compiler is clang-cl rather than `cl.exe`, is in
[Building on Windows](../windows.md#what-you-need).

Install just-makeit into that Python, with pip or with uv:

```powershell
py -m pip install just-makeit
uv tool install just-makeit
```

Visual Studio ships CMake and Ninja, so nothing else is needed.

---

## 1. Scaffold a project and a component

From a PowerShell prompt, in the folder that will hold the project:

```powershell
jm new my_vs
cd my_vs
jm object scale --state factor:double:2.0 --arg-type double --return-type double
```

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

## 3. Create the project's `.venv`

The presets name the project's own interpreter, `.venv\Scripts\python.exe`,
so it must exist, with numpy, before the first configure:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install numpy
```

With uv, `uv venv` then `uv pip install numpy` does the same.
[Building on Windows](../windows.md#the-projects-venv) explains why the
presets name an interpreter rather than search for one.

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

If the configure step fails with `Could NOT find Python3`, the `.venv` is
missing or has no numpy: create it as in step 3, then **Project > Delete
Cache and Reconfigure**.

---

## See also

- [Building on Windows](../windows.md) -- the prerequisites, the presets,
    `CMakeUserPresets.json` for your own, and line endings
- [Build & Run](../commands/build.md) -- `jm build` and `jm test`, the same
    project through the generated `Makefile`
