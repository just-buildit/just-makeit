## 3. Create the project's `.venv`

The presets name the project's own interpreter, `.venv\Scripts\python.exe`,
so it must exist, with numpy, before the first configure:

```{03_venv.ps1}
```

With uv, `uv venv` then `uv pip install numpy` does the same.
[Building on Windows](../windows.md#the-projects-venv) explains why the
presets name an interpreter rather than search for one.
