## 2. Create the module

```{02_module.sh}
```

`just-makeit module filter` scaffolds the grouping unit:

| Created                                      | Purpose                                                                  |
| -------------------------------------------- | ------------------------------------------------------------------------ |
| `native/src/filter/filter_ext.c`             | C extension — empty, no types yet                                        |
| `native/src/filter/CMakeLists.txt`           | Python module target, plus the module's own `filter_core` OBJECT library |
| `native/inc/my_filters/filter/filter_core.h` | Module-level C API (for `just-makeit function`)                          |
| `native/src/filter/filter_core.c`            | Module-level C implementation                                            |
| `src/my_filters/filter/__init__.py`          | Subpackage init — empty exports                                          |
| `src/my_filters/filter/filter.pyi`           | Subpackage type stub — no classes yet                                    |
| `modules/filter.toml`                        | The module's manifest fragment                                           |

`modules/filter.toml` (pulled in by `just-makeit.toml`'s `include`) holds:

```toml
[module.filter]
objects = []
```

The module is a named slot.  Types are added with `just-makeit object`.
