## 2. Create the module

```{02_module.sh}
```

`just-makeit module filter` scaffolds the grouping unit:

| Created                             | Purpose                                   |
| ----------------------------------- | ----------------------------------------- |
| `native/src/filter/filter_ext.c`    | C extension — empty, no types yet         |
| `native/src/filter/CMakeLists.txt`  | Python module target (no object libs yet) |
| `src/my_filters/filter/__init__.py` | Subpackage init — empty exports           |

`just-makeit.toml` gains:

```toml
[module.filter]
objects = []
```

The module is a named slot.  Types are added with `just-makeit object`.
