## 5. Build

| Visual Studio         | Command line                             |
| --------------------- | ---------------------------------------- |
| **Build > Build All** | `cmake --build --preset windows-release` |

```{05_build.ps1}
```

The build compiles the C core, its CTest executables and the Python
extension, and writes the extension (`.pyd`) into `src\my_vs\`, next to its
`.pyi`.
