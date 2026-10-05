## 3. Declare the same data three ways

```{03_declare.sh}
```

Three things to notice, because each one is a trap if you meet it later:

- **Every shape takes YOUR struct as the return type**, not a scalar. A
    scalar there is the mistake worth naming: `--single` with
    `--return-type double` would emit a binding that reads `_r.n` off a
    `double`, and the plain shape would emit `results[i].index` off one.
    Neither could compile, so the CLI refuses both (gh-1064), naming the fix:
    for `--single`,
    `--return-type must be the C struct that the --single record of the result is, not 'double'`.
- **`--record-name` / `--record-doc` only apply to `--single`.** They name and
    document the record *type*, and the other two shapes have no type to name:
    one hands back an `ndarray`, the other a plain `list`.
- **`peaks` does NOT pass `--variable-output`.** Its count comes back as the
    return value of the kernel, which is what `size_t` + `max_results` is for.
    The flag belongs to the `record_dtype` shape, whose kernel fills a caller
    sized `out` buffer and needs a `read_max_out()` companion to size it.
    Passing it here is refused by the CLI (gh-1064): with a plain
    `result_fields` kernel there is nothing for it to size.

`--single` and `--record-dtype` are mutually exclusive; the CLI rejects the
pair.
