- **A refused `jm apply` no longer rewrites your manifest** (gh-1660).
    `apply` recorded `[project] jm_version` before asking any of its
    refusals, so on a project an older jm had generated, every refusal --
    a `c_prefix` collision, an unsupported type, an unknown `--only`, a
    fragment that would lose code -- still left the running version in
    the manifest, a record that this jm had generated a project it had
    refused to touch. The stamp now follows the last refusal. A fragment
    composed by `jm apply <fragment.toml>` is undone too when a refusal
    follows it: the copy in `objects/` and the manifest's `include` line
    are put back. And `jm upgrade` refuses a removed `c_prefix` before it
    writes anything, where it used to rename `jb.toml` first.
