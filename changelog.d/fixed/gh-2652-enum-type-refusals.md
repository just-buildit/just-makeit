- **A refused enum type names the form that works.** A property's
    `--type string_enum:a,b`, a manifest property row spelled
    `type = "enum:<name>"`, an enum `--param` on `jm function` or
    `jm method`, and an enum `--state` field were each refused with a bare
    list of scalar types. Each refusal now gives that surface's enum
    spelling and, for an inline `string_enum:` list, the `[[enum]]` table
    to declare with its values filled in (gh-2652).
