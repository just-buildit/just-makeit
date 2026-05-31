# Next session — handoff

Snapshot after **v0.14**. Pick this up to know where to start.

______________________________________________________________________

## TL;DR

- **Phases 0–2 shipped.** Every common TOML key has a CLI flag; the
    [`docs/configuration.md`](../configuration.md) CLI ↔ TOML mapping is
    the acceptance record.
- **v0.14 landed the sacred/glue contract.** `jm apply` regenerates
    glue (`<comp>_ext.c`, `<comp>.pyi`, `CMakeLists.txt`) from the
    manifest, injects any missing `<comp>_core.h` method/property
    declaration while keeping the inline `step()` body and state struct
    sacred, and never splices the sacred `<comp>_core.c` (a new state field
    or signature change rebuilds via `jm add` / `jm regenerate`). See
    [`declarative-scaffolding.md`](declarative-scaffolding.md).
- **`jm regenerate <component>`** is the deliberate-refresh half:
    deletes the component's files and re-applies from the manifest
    (leaves the manifest alone, unlike `jm remove`). `--force` skips
    the confirm. Discards hand-written `_core.c` — `git stash` first.
- **`--impl` is kept and recommended.** New `--impl file::N:M` lifts a
    line range instead of a named function; composes with `--replace`
    and with TOML `impl_file = "path::N:M"`.
- **`jm wizard` was cut.** No interactive surface; the preset pages +
    CLI cover it. See [`wizard-design.md`](wizard-design.md).
- **Foot-guns fixed:** array *return* (`T[] -> T[]`) errors cleanly
    instead of crashing (still unsupported; array *input* works);
    `jm new --object X --no-step` / `--no-state` work in one command;
    `bool` is a usable scalar slot; `jm object --module M --perf`
    writes `jm_perf.h`; `jm add` preserves init_params; the
    `generator` preset builds (void-arg defaults to a complex return).

______________________________________________________________________

## Working presets

`jm object --preset NAME`: `processor` (default), `generator`,
`consumer`, `reader` all run end-to-end. `blockwise` is intentionally
**excluded** — array return is unsupported. `jm function` is its own
verb.

______________________________________________________________________

## What's left

### Bind expansion (`_bind.py`)

`jm bind` today handles the **processor** shape (scalar state + inline
`_step`). Extend the parser to recognise:

- Methods (any `<comp>_<verb>(...)` declared in the header).
- Init params (ctor args whose names don't match state fields).
- Output-param naming heuristic: `out` / `output` / `dst` / `dest`.
- Variable-output pairing: `<comp>_<verb>_max_out` sibling.
- Opaque state (forward decl in header; definition in `.c`).

Wire `jm bind --check` into every bundled example's CI as a parity
gate. Reference: [`bind-design.md`](bind-design.md).

### Phase 2 polish

1. **Shared `validate_slot()` helper** in `_types.py` so every CLI
    handler and the bind parser produce identical type-rejection
    errors. Good place to add the direct validator tests Codecov keeps
    flagging.
1. **Factor common flag parsing** out of `_cli_object.py` /
    `_cli_method.py` / `_cli_function.py` into `_cli_parse.py`.

______________________________________________________________________

## Design doc index

| Doc                                                        | Answers                                                               |
| ---------------------------------------------------------- | --------------------------------------------------------------------- |
| [`implementation-plan.md`](implementation-plan.md)         | The phased roadmap; what stays, what goes, success criteria.          |
| [`declarative-scaffolding.md`](declarative-scaffolding.md) | The sacred/glue `jm apply` contract, `jm regenerate`, split TOMLs.    |
| [`bind-design.md`](bind-design.md)                         | How `jm bind` synthesises `_ext.c` from a hand-written `_core.h`.     |
| [`wizard-design.md`](wizard-design.md)                     | The retired wizard — historical note only.                            |
| [`../decision-tree.md`](../decision-tree.md)               | "Which command do I want?" flat lookup.                               |
| [`../templates/index.md`](../templates/index.md)           | "What does each preset produce, and which types does each slot take?" |
| [`../types.md`](../types.md)                               | "Which types are legal in which slot?"                                |
| [`release-checklist.md`](release-checklist.md)             | How to cut a release.                                                 |

______________________________________________________________________

## Quick-start commands

```sh
cd ~/just-makeit
git checkout main && git pull

# Pick a thread:
#   bind:   edit _bind.py to recognise methods + init_params + opaque state
#   polish: _types.validate_slot() helper; factor _cli_parse.py
```
