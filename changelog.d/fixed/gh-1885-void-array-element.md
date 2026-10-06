- **A method that returns an array of `void` is refused, before anything is
    written** (gh-1885). Three shapes were accepted: a `--variable-output`
    method with `--return-type void` and nothing else naming its element,
    `jm object --variable-output` on a `consumer` or a `generator` (or any
    `--arg-type void` with no `--return-type`), and a `--batch` method with
    `--return-type void`. jm wrote the method's row, then crashed with
    `KeyError: 'void'` rendering the binding, and every later `jm status`
    and `jm apply` crashed on the manifest it had just written. Each now
    exits 1 with one `error:` line that names the method and the flag that
    names its element, and leaves the project untouched. A manifest that
    already holds such a row gets the same refusal from `jm apply` and
    `jm status` instead of a traceback; `jm remove method` clears it.
