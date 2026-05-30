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
schema = "6"

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

`--impl path/to/file.c::funcname` lifts a C function body from an
external file into the generated `/* <<IMPLEMENT>> */` placeholder.
`--impl path/to/file.c::N:M` instead lifts lines `N`..`M` (inclusive,
1-based) when there is no named function to target. Both compose with
`--replace`. A declarative spec should also be able to carry the
implementation **itself** — so one TOML produces a complete, buildable
component with nothing to wire up by hand.

`--impl` is a **supported, recommended** feature, not a legacy hazard.
The old risk was splicing into an existing, hand-edited file; the
sacred/glue contract eliminates it — `--impl` feeds the generated stub,
and the sacred `_core.c` is the user's once it exists.

Two forms, either of which fills the `/* <<IMPLEMENT>> */` placeholder
for its target:

```toml
[agc]
arg_type = "float _Complex"
return_type = "float _Complex"

# Inline body — a TOML literal multi-line string ('''…'''). Literal,
# not basic: no escape processing, so C backslashes and quotes pass
# through verbatim. This is the heredoc form — paste the body in.
impl = '''
double p = state->power;
p += (cabsf(x) * cabsf(x) - p) * state->alpha;
state->power = p;
return x * (float)agc_gain_(state, p);
'''

[[agc.methods]]
name = "execute_ctrl"
arg_type = "float _Complex"
return_type = "size_t"
# File-reference form — the existing --impl "path::funcname" semantics.
impl_file = "src/agc/agc_ref.c::agc_execute_ctrl"
# Optional pre-injection substitutions — the existing --replace.
replace = { "agc_execute_ctrl" = "execute_ctrl", "TODO" = "done" }
```

- `impl` — the C body inline (TOML literal string). The heredoc form.
- `impl_file` — `"path::funcname"` (named function) or `"path::N:M"`
    (line range `N`..`M`, inclusive, 1-based), the file-lift behaviour.
- `replace` — a table of `old = new` substitutions applied before
    injection (the existing `--replace`); valid with either form.
- `impl` and `impl_file` are **mutually exclusive** on one target —
    `apply` errors if both are set.
- Both are valid on the object section (the `step()` body) and on each
    `[[X.methods]]` entry (that method's body).

**TOML ownership vs. sacred `.c`.** When a target carries
`impl`/`impl_file`, the TOML *owns* that body: `apply` writes it and a
re-`apply` re-asserts it, even into the otherwise-sacred `_core.c`.
When neither is set, the sacred-file rule applies — `apply` never
overwrites a hand-written body in `_core.c`. A body is declared in the
TOML *or* edited in the C file, never both.

`jm script` round-trips `impl_file` as the reference it is, and emits an
`impl` body as the literal `'''…'''` block.

______________________________________________________________________

## `jm apply` — the sacred/glue contract

Materialize everything *declared* in the TOML.

```sh
jm apply                  # materialize the project's own TOML
jm apply objects/dsp.toml # merge a fragment in, then materialize
```

- No argument → read the project manifest + its includes, generate
    every file each object/module/function implies.
- A path argument → a **compose fragment**: an object TOML that is
    copied into `objects/` (so the project stays self-contained), added
    to the `include` set, then materialized.
- `apply` **never deletes.** It is safe to run repeatedly; deletion is
    strictly `jm remove`'s job.

`apply` shipped a precise per-file policy. Every file an object owns
falls into one of three buckets:

| File                   | Class      | What `apply` does                                                                 |
| ---------------------- | ---------- | --------------------------------------------------------------------------------- |
| `<comp>_ext.c`         | **glue**   | Regenerated from the manifest on every apply.                                     |
| `src/<pkg>/<comp>.pyi` | **glue**   | Regenerated from the manifest on every apply.                                     |
| `CMakeLists.txt`       | **glue**   | Regenerated from the manifest on every apply.                                     |
| `<comp>_core.h`        | **hybrid** | Public declarations refresh; the inline `step()` body and state struct preserved. |
| `<comp>_core.c`        | **sacred** | Never overwritten once it exists. `steps()` / lifecycle bodies are the user's.    |

So editing the manifest always propagates to the glue. The **hybrid**
header means a TOML-declared method or field reaches the public API on
the next apply, while the hand-written inline `step()` body and the
state struct definition survive. The **sacred** `.c` is the one file
`apply` will not touch — a signature change you make in TOML may need
an additive verb (`jm method`, `jm add`) or a `jm regenerate` to also
update the sacred body.

When a target declares `impl` / `impl_file` (see
[Implementation bodies](#implementation-bodies)), the TOML *owns* that
body and `apply` re-asserts it even into the otherwise-sacred `.c`.

This is the batch-scaffold path: author a complex object in one TOML,
`jm apply` it. It also makes a project reproducible from its TOML alone
— a stronger guarantee than `jm script` (which only replays CLI
history).

______________________________________________________________________

## `jm regenerate`

The deliberate-refresh half of the sacred/glue contract.

```sh
jm regenerate <component>          # confirm, then rebuild from the manifest
jm regenerate <component> --force  # skip the confirmation
```

- Deletes **every file the component owns** and re-runs `jm apply` to
    rebuild them from the manifest — including the sacred `_core.c`.
- Single confirmation prompt; `--force` skips it.
- Leaves the **manifest untouched** (unlike `jm remove`, which strips
    the TOML section). The component still exists; only its generated
    files are rebuilt.
- Discards hand-written `_core.c` bodies — advise `git stash` first.
- Works for both standalone and module objects.

Use it when a manifest edit (a new state field, a changed signature)
needs to reach the sacred `.c` body and the additive verbs don't cover
the change.

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
