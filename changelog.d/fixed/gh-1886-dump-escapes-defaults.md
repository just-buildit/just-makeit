- **A `const char *` param default no longer breaks every later `apply`;
    every string jm writes into a manifest is escaped** (gh-1886).
    `jm method o greet --param 's:const char *="hi"'` -- the documented
    spelling, a C string literal -- succeeded, and then every `jm apply` and
    `jm status` failed with "just-makeit generated a manifest it cannot read
    back": the serializer they replay through wrote the default between
    quotes it never escaped. That was the house style for 130-odd values,
    not one: every `default`, `name`, `type`, `header`, C expression,
    `help` text and array entry outside the prose keys. All of them now go
    through the one escaper, so a `"`, `\` or newline in any value reads
    back as written -- including the hand-written `default = '""'` that
    jm's own refusal recommends for an empty string, which failed the same
    way. A C body (`impl`, `create_impl`, `reset_impl`, `destroy_impl`,
    `init_post_parse`) keeps its backslashes: `printf("a\n")` used to read
    back with a real newline inside the C string literal, and `'\0'` made
    the write refuse itself. And `init_post_parse` stays on its component:
    it was written after the `init_params` rows, so a rewrite through the
    serializer (`jm split-objects`, a new fragment) moved it onto the last
    of them.
