## 1. Scaffold a project and a component

From a PowerShell prompt, in the folder that will hold the project:

```{01_scaffold.ps1}
```

`scale` is one object with one piece of state, `factor`, and a `step()` that
takes and returns a `double`. Its C core is
`native\inc\my_vs\scale\scale_core.h`, and the project root holds the
`CMakePresets.json` the next steps use.
