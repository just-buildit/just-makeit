- **`strict`, `count_default` and `count_name` are refused on the wrong
    side of a method's input** (gh-2313). `strict` reads an input array,
    and `count_default` / `count_name` the count jm synthesizes for an
    input-less `--variable-output` or `--batch` method; each was accepted
    on every method and did nothing on the others. `jm method` and `apply`
    now refuse each where the method has nothing for it to read.
