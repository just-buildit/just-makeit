- **Every text file jm reads, and every one its tests and examples read or
    write, names its encoding** (gh-2654). A `read_text()`, `write_text(s)`
    or text-mode `open()` with no `encoding=` uses the platform codec,
    which is cp1252 on Windows: a test writing a header that held an em
    dash failed only on the Windows leg. 1360 such calls now pass
    `encoding="utf-8"`, among them jm's own reads of manifests and
    generated files, which on Windows decoded UTF-8 as cp1252. A new
    `make lint` gate, `text-encoding-check`, refuses one that omits it.
