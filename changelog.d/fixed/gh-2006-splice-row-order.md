- **`jm apply` splices a new method, `extra_methods` row or property into a
    sacred module fragment where a render puts it, so `jm adopt --check`
    no longer reports jm's own splice as `differs`** (gh-2006). Every spliced
    row went before the `{NULL}` sentinel, written at the sentinel's `{`:
    the first row sat at two indents and the sentinel at none, and a method
    row landed after `destroy` / `__enter__` / `__exit__`, where a render
    puts it before them. So a fragment `adopt --check` had called safe read
    `differs: table:PyMethodDef` one `apply` later, on a table only jm had
    touched. A row now goes before the row that follows it in the render, at
    the table's indent, and the spliced method and getset tables equal a
    fresh render byte for byte. The re-render that carries a hand-written
    row into a sacred fragment (`jm method`, `jm property`) keeps that row
    where it was written, before the row that followed it, instead of moving
    it behind jm's trailing built-ins.
