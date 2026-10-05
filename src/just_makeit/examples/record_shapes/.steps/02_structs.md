## 2. Declare the record structs — they are yours, not jm's

```{02_structs.py}
```

Declare them before you build: `--return-type evlog_summary_t` and
`--record-dtype evlog_rec_t` only name these types. jm writes them into
prototypes and never checks or reads them, so the compiler is the first to
need them, ahead of those prototypes in the header.
