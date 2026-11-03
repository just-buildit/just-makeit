- **`just_makeit.components(cfg)` and `just_makeit.Refusal` are public**
    (gh-2452). `components` lists a manifest's component names -- every
    top-level table but the reserved sections and `include` -- so a
    script reading a `load_manifest` result no longer imports the
    private `_config.components` or spells its own copy of the reserved
    set, which is how gh-2434 filed `[[enum]]` and `[codec]` under
    `objects/`. `Refusal` is the `ValueError` `load_manifest` raises for
    a manifest jm refuses; catch it by name to read every problem from
    `.errors`. Both are the private objects re-exported, not copies, and
    `load_manifest`'s docstring now cites only public names.
