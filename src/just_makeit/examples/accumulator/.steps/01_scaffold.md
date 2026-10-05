## 1. Scaffold the project

```{01_scaffold.sh}
```

`just-makeit new` with no `--object` creates the project skeleton only:
`CMakeLists.txt`, `pyproject.toml`, `just-makeit.toml`, and the `native/`
directory tree.  No component yet.

`just-makeit module accumulator` adds a named module slot:

| Created                                            | Purpose                                                       |
| -------------------------------------------------- | ------------------------------------------------------------- |
| `native/inc/my_acc/accumulator/accumulator_core.h` | Module-level C API (for `just-makeit function`)               |
| `native/src/accumulator/accumulator_core.c`        | Module-level C implementation                                 |
| `native/src/accumulator/accumulator_ext.c`         | C extension (empty, no types)                                 |
| `native/src/accumulator/CMakeLists.txt`            | Python module target, plus the `accumulator_core` OBJECT lib  |
| `src/my_acc/accumulator/__init__.py`               | Subpackage init (no types yet)                                |
| `src/my_acc/accumulator/accumulator.pyi`           | Type stub                                                     |
| `modules/accumulator.toml`                         | The module's manifest fragment                                |

`modules/accumulator.toml` (pulled in by the `include` line in
`just-makeit.toml`) gains:

```toml
[module.accumulator]
objects = []
```

Objects are added with `just-makeit object` next.
