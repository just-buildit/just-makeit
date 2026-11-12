## 1. Install jm, then scaffold a project and a component

From a PowerShell prompt, in the folder that will hold the project:

```{01_scaffold.ps1}
```

Run in a project's root, it installs into that project's `.venv`; anywhere else, if `jm` is not found in a new shell, run `jbx update-path` once.

The first line installs jm, the same in Windows PowerShell and
PowerShell 7: from PowerShell,
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
runs jm's Windows installer,
`install.ps1`, in this window. This folder is not a project's root yet, so
it installs just-makeit into a per-user venv under `%LOCALAPPDATA%`, copies
its launchers into `%USERPROFILE%\.local\bin`, and puts that folder on this
window's `PATH`, so `jm` runs on the next line. It activates no venv: the
project's own `.venv` comes in step 3, and an active venv would change what
`py` and `uv` pick.
[Building on Windows](../windows.md#installing-jm) says more.

`scale` is one object with one piece of state, `factor`, and a `step()` that
takes and returns a `double`. Its C core is
`native\inc\my_vs\scale\scale_core.h`, and the project root holds the
`CMakePresets.json` the next steps use.
