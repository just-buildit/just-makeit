## 2. Declare the record structs — they are yours, not jm's

```{02_structs.py}
```

This has to happen first. `--return-type evlog_summary_t` and
`--record-dtype evlog_rec_t` name types that must already exist in the sacred
header; jm puts them in prototypes and never looks inside them.
