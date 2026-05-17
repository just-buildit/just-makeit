## 1. Scaffold the project

```{01_scaffold.sh}
```

`just-makeit new` with no `--object` creates the project skeleton only:
`CMakeLists.txt`, `pyproject.toml`, `just-makeit.toml`, and the `native/`
directory tree.  No component yet.

`just-makeit module accumulator` adds a named module slot:

| Created                                   | Purpose                       |
| ----------------------------------------- | ----------------------------- |
| `native/src/accumulator/accumulator_ext.c`| C extension (empty, no types) |
| `native/src/accumulator/CMakeLists.txt`   | Python module target          |
| `src/my_acc/accumulator/__init__.py`      | Subpackage init (empty)       |

`just-makeit.toml` gains:

```toml
[module.accumulator]
objects = []
```

Objects are added with `just-makeit object` next.
