- **A whole-manifest rewrite keeps every key the manifest holds, as the
    type it is** (gh-2045, gh-2046, gh-2036, gh-2047). `jm split-objects`,
    `jm migrate-to-fragments`, a brand-new fragment and an install without
    tomlkit all write the manifest through one writer, and it had a
    hand-written emitter per table that fell a key behind each time a key
    was added. A method's and a module function's params lost `doc`,
    `enum`, `out`, `rank`, `elements_per_sample` and `str_hint`. An object
    lost `records`, `fragment` and its `*_impl_file` keys; a method lost
    `status_errors` and `releases`; a plain module lost `platforms`; an
    `[[enum]]` row lost `enumerators`. `streamable = true` came back as
    `"True"`, which reads as false, so `stream()` disappeared on the next
    `apply`. An `array_args` row's `dtype` was respelt `type`. A top-level
    value written after a table bound to that table, and a key that needs
    quoting, such as a re-exported sub-package with a dot in its name, was
    written bare. Now one emitter writes every table: every key, each as its
    own TOML type (`true`, `64`, an array, an inline table), keys quoted
    where TOML needs it, and top-level values before any table. Layout is
    unchanged for anything that was written correctly before. An empty
    value is written too, so a stateless object's new fragment says
    `state = []`, as the central manifest always has. It leaves out
    only a key beginning `_` (run-time state, never authored), a value of
    None, and a field group's expansion (its declaration is what is kept).
    It reads its own text back and refuses, naming the value, to write one
    that means anything else. Two old rewrites are gone, because they changed
    what the author wrote: a function param's `mutable` is no longer respelt
    `out`, and a hand-written `out_divisor = 1` is no longer dropped.
