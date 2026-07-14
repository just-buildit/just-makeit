# Declarative scaffolding — design

Status: **shipped.** Split per-object TOMLs and `jm remove` landed in
v0.13.5 (schema 6); the sacred/glue `jm apply` contract and
`jm regenerate` landed in v0.14. For the user-facing walkthrough with
diagrams, see
[../declarative-scaffolding.md](../declarative-scaffolding.md). The
bundled `declarative_scaffold` example is the runnable proof of the
end-to-end story.

______________________________________________________________________

## Motivation

Today every just-makeit command is *additive and imperative*:

- `object`, `module`, `method`, `property`, `function`, `add` each mutate
    `just-makeit.toml` and generate files as a side effect.
- There is **no way to remove** anything — you hand-edit the TOML and
    delete files yourself.
- There is **no way to batch-scaffold** a complex object — you run one
    CLI call per state var, method, and property.
- `just-makeit.toml` is a **single monolithic file**; a project with
    many objects has one large, merge-conflict-prone manifest.

`just-makeit.toml` is *already* a complete declarative description of the
project — the CLI just writes it as a side effect. The gap is the
reverse direction: author the spec, then materialize the project from
it. These three features close that gap as one coherent model.

______________________________________________________________________

## The model

A project's TOML is the **desired state**. Objects can live in their own
files; a command materializes (`apply`) or tears down (`remove`) the
generated code to match.

### TOML layout

```toml
# just-makeit.toml — thin manifest
[project]
name = "doppler"
schema = "7"

[module.spectral]
objects = ["fft", "fft2d"]

# Pull object specs in from their own files.
include = ["objects/*.toml"]
```

```toml
# objects/agc.toml — one object, its whole spec
[agc]
arg_type = "float _Complex"
return_type = "float _Complex"
class_name = "AGC"

[[agc.init_params]]
name = "ref_db"
type = "double"
default = "0.0"

[[agc.properties]]
name = "clip_db"
type = "double"
writable = true
field = true
```

- `include` accepts globs and/or explicit paths, relative to the
    manifest.
- An included file holds one or more top-level object sections (and may
    hold `[[module.X.functions]]` entries for module-level functions).
- **Backward compatible**: no `include` key → today's single-file
    behaviour, unchanged. The split layout is opt-in.

### `_config.py` — the load/save split

`_config.py` is the only reader/writer of project TOML. It changes in
two ways:

**Read (easy).** `load()` reads the manifest, resolves `include`, and
merges every file into the single dict that all consumers already
expect. Nothing downstream changes — `components()`, `init_params()`,
the templates, etc. all see one merged config.

**Write (the real work).** Mutating commands currently call `save()`,
which rewrites the whole `just-makeit.toml`. With split files, each
mutation must route back to the file that **owns** that object. So
`load()` must record **provenance** — for every top-level section, which
file it came from — and `save()` must write each section back to its
origin file, preserving formatting.

- A mutation to `[agc]` (e.g. `jm method ... --object agc`) rewrites
    `objects/agc.toml`, not the manifest.
- A new object (`jm object foo`) is written to a new file
    (`objects/foo.toml`) when the project uses the split layout, or
    appended to the manifest when it does not.
- `[project]` and `[module.X]` declarations always live in the
    manifest.

Provenance is in-memory only — never serialized.

______________________________________________________________________

## Implementation bodies

A declarative spec should be able to carry the implementation **itself** —
so one TOML produces a complete, buildable component with nothing to wire up
by hand. `--impl`/`impl_file` (and their `replace` companion) are what make
that possible; the mechanics, the two forms (named function vs. line range),
the mutual-exclusivity rule, and the TOML-ownership-vs-sacred-`.c` behavior
are documented for users on the top-level page, in
[The fragment](../declarative-scaffolding.md#the-fragment).

The rationale worth recording here: `--impl` is a **supported, recommended**
feature, not a legacy hazard. Before the sacred/glue contract shipped, the
risk was splicing generated output into an existing, hand-edited file — a
fragile brace-matching problem. The contract eliminates that risk entirely by
construction: `--impl`/`impl_file` only ever feed the *generated* stub before
the sacred `_core.c` exists, and once it exists it's the user's, full stop.
That's why lifting an implementation from an external file was safe to keep
(and lean into) rather than deprecate alongside the old splicer.

______________________________________________________________________

## `jm apply` and `jm regenerate` — the sacred/glue contract

The mechanics — the per-file bucketing (glue / mixed / sacred), the CLI
flags, the fragment-compose form of `apply`, `regenerate`'s default
splice-back behavior (gh-267) vs `--discard` — are documented once, for
users, on the top-level page:
[What `jm apply` does](../declarative-scaffolding.md#what-jm-apply-does) and
[`jm regenerate` — the deliberate refresh](../declarative-scaffolding.md#jm-regenerate-component-the-deliberate-refresh).
This section only keeps the design rationale that doesn't belong in a
user-facing walkthrough.

- **Why `apply` never deletes.** Keeping deletion out of `apply` means a
    reconcile is always safe to re-run — the only way to lose a generated
    file is the explicit `jm remove`. Splitting "reconcile" and "delete" into
    two verbs was a deliberate decision (see [Decisions](#decisions) below),
    not an accident of implementation order.
- **Why `regenerate` exists instead of teaching `apply` to rebuild.** `apply`
    is additive by invariant — teaching it to also tear down and rebuild a
    sacred file on a structural change would mean two different classes of
    side effect live behind one verb, and a user could no longer tell from
    the command name alone whether their `_core.c` is at risk. `regenerate`
    keeps that risk explicit and opt-in.
- **`impl`/`impl_file` overrides the sacred rule, deliberately.** When a
    target declares one, the TOML — not the `.c` file — is the source of
    truth for that body, so `apply` re-asserts it even into an otherwise
    untouchable file. This is the one place the sacred/glue split bends: it's
    load-bearing for reproducibility (a project should be rebuildable from
    its manifest alone, a stronger guarantee than `jm script`, which only
    replays CLI history), so the exception is scoped tightly to targets that
    opt in.
- **The lift/splice-back behavior (gh-267) reuses `apply`'s own machinery.**
    `jm regenerate`'s default preserve-by-splicing pass extracts and restores
    function bodies by name using the same by-name extract/restore code
    `jm apply` already uses to keep hand-patched module `_ext.c` glue intact
    — one splicer, two call sites, instead of a second implementation to
    keep in sync. `jm add` and `jm remove --state` opt out (`discard=True`)
    because a state change makes the old body's signature stale before the
    splice would even run.

______________________________________________________________________

## `jm remove`

The explicit, destructive counterpart. Kept separate from `apply` so
deletion is always deliberate, never an inferred side effect of a
reconcile.

```sh
jm remove object <name>
jm remove module <name>
jm remove method <name>   --object <obj>
jm remove property <name> --object <obj>
jm remove function <name> --module <mod>
```

- Syntax mirrors the additive commands.
- **object / module** — delete the generated `native/inc/<x>/`,
    `native/src/<x>/`, `src/<pkg>/<x>/`; strip `add_subdirectory` /
    `target_sources` from the top `CMakeLists.txt`; drop the TOML section
    (and the object's file, if split).
- **method / property / function / state** — drop the TOML entry and
    re-run the existing regeneration for the affected `ext.c` / `core.h` /
    `.pyi`.
- **Safety**: prompt for confirmation; `--force` skips the prompt.
    Warn explicitly when removal will delete a `core.c` / `core.h` that
    holds hand-written (preserved) bodies.

______________________________________________________________________

## Migration

- New schema version (6) gates the `include` key.
- A project stays single-file until it opts in. A future
    `jm split-objects` helper (or `jm apply --split`) could move inline
    `[object]` sections out into `objects/<name>.toml` and add the
    `include` glob.
- `jm upgrade` needs no destructive step here — the feature is additive
    to the schema.

______________________________________________________________________

## Decisions

Resolved 2026-05-19:

1. **Compose fragment placement** — `jm apply <fragment>` **copies** the
    fragment into `objects/` and adds it to `include`. The project stays
    self-contained; the external file is no longer needed afterwards.
1. **Reconcile-with-delete** — `jm apply` **never deletes**. Deletion is
    strictly `jm remove`'s job. The two commands stay split so deletion
    is always an explicit, deliberate act.
1. **Conflict on apply** — if a fragment defines an object that already
    exists, `apply` **errors** — and the error names a specific remedy,
    e.g. *"object `foo` already exists; run `jm remove object foo` first,
    or rename the object in the fragment."* Never silently overwrite.
1. **Formatting preservation** — `save()` must write each file back with
    a **format-preserving TOML writer**, not a plain dump, so a mutation
    to one object's file produces a minimal diff and never churns the
    manifest or sibling object files.
1. **Inline implementations** — an object/method spec may carry its C
    body inline via `impl` (a TOML literal `'''…'''` heredoc) or by
    reference via `impl_file` (`"path::funcname"` or `"path::N:M"`, the
    `--impl` semantics). The two are mutually exclusive. When either is
    set, the TOML owns the body and `apply` re-asserts it; otherwise the
    sacred-file rule keeps hand-written `_core.c` untouched.
