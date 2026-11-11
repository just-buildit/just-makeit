## 3. Create the project's `.venv` -- before opening Visual Studio

The presets name the project's own interpreter, `.venv\Scripts\python.exe`,
so it must exist, with numpy, before the first configure. From the same
prompt, in `my_vs`:

```{03_venv.ps1}
```

`uv sync` reads the project's `pyproject.toml`, creates `.venv`, and
installs numpy, the project's dependency, and the project itself, editable.
That install compiles nothing; the build is step 5. It also writes
`uv.lock`, the resolved versions, beside `pyproject.toml`.
[Building on Windows](../windows.md#the-projects-venv) explains why the
presets name an interpreter rather than search for one.

A venv uv makes has no pip, by design, so a `python -m pip install` line
from another page does not work in it: add packages with `uv add`, or
`uv pip install`. Do not run `py -m venv .venv` over it either; that does
not add pip. The [pip page](visual_studio_pip.md) is the route without uv.
