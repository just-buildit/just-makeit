## 3. Create the project's `.venv` -- before opening Visual Studio

The presets name the project's own interpreter, `.venv\Scripts\python.exe`,
so it must exist, with numpy, before the first configure. From the same
prompt, in `my_vs`, with no virtual environment active:

```{03_venv.ps1}
```

The first line makes the venv, with pip in it; the second installs numpy
into it. [Building on Windows](../windows.md#the-projects-venv) explains why
the presets name an interpreter rather than search for one.

Only on a project with no `.venv` yet. A `.venv` that uv made -- `uv sync`
makes one -- has no pip, and running `py -m venv .venv` over it does not add
one: keep it, and follow the [uv page](visual_studio_uv.md) instead.
