## 4. Open the folder and pick a preset

| Visual Studio                                 | Command line                     |
| --------------------------------------------- | -------------------------------- |
| **File > Open > Folder**, and pick `my_vs`    | `cd my_vs`                       |
| Toolbar preset list: **Release (clang-cl)**   | `cmake --preset windows-release` |

Visual Studio reads `CMakePresets.json`, loads the Microsoft build environment
itself, and configures as soon as the preset is chosen. The same configure
from a **Developer PowerShell for VS**, which loads that environment:

```{04_configure.ps1}
```

Open the folder only once step 3 has made `.venv`. Opened first, the
configure stops with `Python3_EXECUTABLE is ...\.venv\Scripts\python.exe,
which does not exist`, and Visual Studio keeps that failed configure: create
the `.venv` as in step 3, then **Project > Delete Cache and Reconfigure**.

The preset alone chooses clang-cl, Ninja and the `.venv` interpreter; nothing
in the environment has to. The build tree is `out\build\windows-release\`.
**Debug (clang-cl)** and **RelWithDebInfo (clang-cl)** are the same with
`windows-debug` and `windows-relwithdebinfo`.
