- **`jm script` names every param key its command line cannot spell**
    (gh-1765). A method, module function or constructor param was replayed
    as `--param name:type` (or `--init-param name:type:...`), and the rest of
    its manifest row was dropped with no word: `rank` and
    `elements_per_sample`, so the replayed project lost its rank guard and
    interleave divisor, plus `doc`, `str_hint` and others. Only `enum` on a
    method param got a `# NOTE`. Each param spelling now reports the keys it
    carries, and a `# NOTE` before the command names every other key the row
    declares, on method, view-method, function, object and view init params.
    A key added to the manifest later is covered without a change here. A
    method param's `default`, which `jm method --param name:type=<default>`
    can spell, is now emitted instead of dropped.
