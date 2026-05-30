# Next session — handoff

Snapshot at the end of the **v0.13.23 release** session. Pick this up
to know where to start.

______________________________________________________________________

## TL;DR

- **Released**: v0.13.23. 11 new CLI flags (full Phase 2 stack:
    PRs #74, #75, #76, #77, #78, #79, #80), Complete CLI ↔ TOML mapping
    in [`docs/configuration.md`](../configuration.md), gallery
    reframed around generic data-flow shapes (`processor`, `blockwise`,
    `generator`, `consumer`, `reader`, `function`). `detector` preset
    dropped; variable-output is now a capability flag.
- **Phase 2 acceptance bar hit**: every common TOML key has a CLI
    flag. ~66 keys reachable via CLI; ~15 stay TOML-only by design.
- **Phase 1 and 2 complete** in
    [`implementation-plan.md`](implementation-plan.md). Phase 3 has
    three parallel threads, none started yet.

______________________________________________________________________

## What's next — Phase 3

Three threads run in parallel; pick whichever fits the session.

### Thread 3a — Preset flags on `jm object`

Land `--preset {processor|blockwise|generator|consumer|reader}` so the
gallery's six shapes are one-flag-each. The renderer side already
exists — each preset is a labelled bundle of existing flags + a
hand-tuned `_core.c` skeleton.

| Preset      | What `--preset NAME` does                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------------------------- |
| `processor` | Marker / alias for the existing default. Documented.                                                           |
| `blockwise` | `--arg-type "T[]" --return-type "T[]"` + block-shaped `steps()` skeleton with the for-loop pre-written.        |
| `generator` | `--arg-type void` + `steps(n)` producer skeleton.                                                              |
| `consumer`  | `--return-type void` + accumulator skeleton.                                                                   |
| `reader`    | `--no-step --init-param filepath:"const char *"` + `read()` / `seek()` / `close()` methods registered upfront. |

`jm function` is its own verb (not a `--preset` flag on `jm object`).

Per-preset skeletons live in (proposed)
`src/just_makeit/templates/c/src/presets/*.template`. Each one is
small (~30 lines of C) and renders through the existing
`_render.render` machinery.

### Thread 3b — Bind expansion

`src/just_makeit/_bind.py` today handles only the **processor** shape
(scalar state + inline `_step`). Extend the parser to recognise:

- Methods (any `<comp>_<verb>(...)` declared in the header).
- Init params (ctor args whose names don't match state fields).
- Output-param naming heuristic: `out` / `output` / `dst` / `dest`.
- Variable-output pairing: `<comp>_<verb>_max_out` sibling → `verb`
    binds as variable-output.
- Opaque state (forward decl in header; definition in `.c`).
- C enums in init params.
- Result-struct outputs (event-emitter shape).

Wire `jm bind --check` into every bundled example's CI as a parity
gate.

Reference: [`bind-design.md`](bind-design.md).

### Thread 3c — `jm wizard`

A new file `src/just_makeit/_wizard.py` (~400 LOC) with plain
`input()` prompts that **runs commands in-process** (never prints
TOML). Phases:

1. Project gate.
1. Preset (the question is "What does your component do?" with the six
    options from the gallery).
1. Types and state (only the questions the preset needs).
1. Optional impl bodies (paste a C body or skip).
1. Extras (perf, external deps).

Reference: [`wizard-design.md`](wizard-design.md).

______________________________________________________________________

## Phase 2 polish (deferred from the release)

Two cleanups that didn't block the release but should land before
Phase 3 starts in earnest:

1. **Shared `validate_slot()` helper.** Every CLI handler today
    inlines its own type check. Refactor into
    `_types.validate_slot(slot: str, type_str: str) -> None` so every
    handler, the bind parser, and the wizard produce identical error
    messages.
1. **Refactor flag-parsing surface.** `_cli_object.py` /
    `_cli_method.py` / `_cli_function.py` each grew 5-10 lines per
    Phase 2 row. Factor common patterns into `_cli_parse.py`. This
    would have caused unnecessary conflicts during the Phase 2 churn —
    safe to do now.

Both are good warm-up tasks before the bigger Phase 3 threads.

______________________________________________________________________

## Risk areas / loose ends

1. **CHANGELOG coverage**: v0.13.23 mentions all 11 flags + the
    gallery reframe. Spot-check before announcing.
1. **Codecov patch coverage** has been red on most Phase 2 PRs because
    the CLI-surface tests don't exercise the validators. The
    `validate_slot()` refactor (Phase 2 polish #1) is the right place
    to add direct tests.
1. **`jm bind` is processor-only.** Phase 3 thread 3b expands it.
    Until then, only `running_stats` actually exercises `jm bind`
    end-to-end.
1. **`--init-param` syntax with spaces in type names**
    (`'filepath:const char *'`) works because the shell quotes it; the
    parser splits on `:` once and takes the rest as the type. Fragile;
    consider a tighter syntax (`name@type` for unambiguous splitting).
1. **PR #82** (the prior session's handoff doc) was superseded by
    this doc and never merged — close it.

______________________________________________________________________

## Design doc index

| Doc                                                                                                  | Answers                                                                                                                                   |
| ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| [`implementation-plan.md`](implementation-plan.md)                                                   | The full phased roadmap. Where every PR fits, what stays, what goes, success criteria.                                                    |
| [`bind-design.md`](bind-design.md)                                                                   | How `jm bind` synthesises `_ext.c` from a hand-written `_core.h`. Parser strategy, contract, phased rollout.                              |
| [`wizard-design.md`](wizard-design.md)                                                               | How an interactive wizard would work — runs commands in-process, never emits TOML.                                                        |
| [`../decision-tree.md`](../decision-tree.md)                                                         | "Which command do I want?" flat lookup.                                                                                                   |
| [`../templates/index.md`](../templates/index.md) + 6 preset pages                                    | "What does each preset produce, and what types does each slot accept/reject?"                                                             |
| [`../types.md`](../types.md)                                                                         | "Which types are legal in which slot?" Five slots, explicit allowlists per slot.                                                          |
| [`../configuration.md` §Complete CLI ↔ TOML mapping](../configuration.md#complete-cli--toml-mapping) | "For every TOML key, what's the CLI flag?" Phase 2 acceptance bar — every common path is ✅; rare modifiers are 🔴 (TOML-only by design). |
| [`release-checklist.md`](release-checklist.md)                                                       | How to cut a release.                                                                                                                     |

______________________________________________________________________

## Quick-start commands for next session

```sh
# 1. Sync.
cd ~/just-makeit
git checkout main && git pull

# 2. Confirm v0.13.23 is on PyPI.
pip index versions just-makeit

# 3. Close PR #82 (superseded by this doc).
gh pr close 82 --repo just-buildit/just-makeit \
    --comment "Superseded by the post-v0.13.23 handoff merged in 0.13.23."

# 4. Pick a Phase 3 thread:
#    3a:  edit _cli_object.py to add --preset {blockwise|generator|consumer|reader}
#         + skeleton templates under src/just_makeit/templates/c/src/presets/
#    3b:  edit _bind.py to recognise methods + init_params + opaque state
#    3c:  create src/just_makeit/_wizard.py
#
# Or warm up with Phase 2 polish:
#    - _types.validate_slot() helper
#    - factor common flag parsing into _cli_parse.py
```

Good luck — every door is wide open.
