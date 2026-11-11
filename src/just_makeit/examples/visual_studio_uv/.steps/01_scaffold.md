## 1. Install jm, then scaffold a project and a component

From a PowerShell prompt, in the folder that will hold the project:

```{01_scaffold.ps1}
```

The first line installs jm, the same in Windows PowerShell and
PowerShell 7: from PowerShell,
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
runs jm's Windows installer,
`install.ps1`, in this window. It installs just-makeit into its own venv,
`%LOCALAPPDATA%\just-makeit\venv`, and puts that venv's `Scripts` folder
first on this window's `PATH`, so `jm` runs on the next line. It does not
activate that venv: the project's own `.venv` comes in step 3, and an active
venv would change what `py` and `uv` pick. In a new window, run the same line
again to get `jm` back; nothing is downloaded when jm is current.
[Building on Windows](../windows.md#installing-jm) says more.

`scale` is one object with one piece of state, `factor`, and a `step()` that
takes and returns a `double`. Its C core is
`native\inc\my_vs\scale\scale_core.h`, and the project root holds the
`CMakePresets.json` the next steps use.
