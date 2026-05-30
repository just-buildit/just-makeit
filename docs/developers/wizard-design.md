# `jm wizard` — design sketch (**RETIRED**)

> **Status: retired, not implemented, not planned.** This document is
> kept for historical context only. The wizard was cut in the
> v0.13.23 retrospective in favour of a smaller surface: a curated
> set of inspectable preset pages + single-shot CLI. See
> [`cli-redesign.md`](cli-redesign.md) for the current direction.
>
> Reasons for the cut:
>
> - **Maintenance burden.** An interactive prompt tree is a new thing
>     to learn, test, and document; it grows shadow branches every
>     time a flag is added.
> - **The pattern set is small.** Five object presets + the function
>     verb. "Read a page, run the matching command" is fewer steps
>     than answering a wizard.
> - **It obscures the canonical surface.** Users learn the wizard
>     instead of the CLI; the CLI is what scripts use.

The rest of this file describes the wizard that *would have been*.
Disregard for current design decisions.

______________________________________________________________________

## (historical) Original sketch

Status: **proposal, not implemented.** Companion to the user-facing
[decision tree](../decision-tree.md), the
[template gallery](../templates/index.md), and the sibling
[`bind-design.md`](bind-design.md). This note captures what an
interactive wizard would do, why it's worth building, and what the
smallest useful version looks like.

The seven shape presets the wizard offers each have their own page in
the [template gallery](../templates/index.md), titled with the exact
CLI invocation that materialises them. Users can browse the actual
generated code before they pick a preset — no pseudocode, no guessing.

If a user prefers to hand-write `<comp>_core.h` and `<comp>_core.c`
directly — matching one of the gallery template shapes by convention
— [`jm bind`](bind-design.md) goes the other direction: reads the
header and synthesises the binding, no TOML and no prior `jm` history
required. Bind and wizard are the two ends of the same axis.

______________________________________________________________________

## Premise

The stumbling block for new users isn't picking the right command — it's
the moment they hit a pattern the CLI flags don't cover and they have to
go author TOML. Every recent bug report (gh-65, gh-68, gh-69, gh-70,
gh-71, gh-72) came from exactly that path: user wanted `init_params + state`, or `out = true` on a function param, or `no_step = true` —
edited the TOML by hand, tripped a real bug in the TOML-only code path.

The TOML is implementation detail. The CLI should be enough. The
wizard's job is to *make sure no one ever has to learn TOML for the
common cases*.

That reframes both halves of this project:

- **Wizard.** Runs commands in-process, in order. Never prints TOML for
    the user to paste; never produces a `jm apply` step. By the time the
    wizard exits, the project is on disk with `/* TODO */` markers where
    the user's algorithm goes.
- **CLI.** Grows the flags the TOML-only patterns need today, so the
    wizard has commands to run. The flags are useful on their own —
    they're not wizard-specific.

The decision-tree page stays as the printed form of the same flow, for
people who skim instead of typing.

______________________________________________________________________

## What the wizard asks (and what runs underneath)

Phase 1: **project gate.** No `just-makeit.toml`? Ask the project name
and run `jm new <name>`. Otherwise jump to phase 2.

Phase 2: **shape preset.** One question with one answer:

```
What does your component do?
  1) processor      input in → output out (1:1)               (defaults)
  2) blockwise      array in → array out                      (--blockwise)
  3) generator      no input; produces output on demand       (--generator)
  4) consumer       takes input; no output                    (--consumer)
  5) reader         opens a file/socket; custom verbs         (--reader)
  6) function       no class; just module-level function(s)   (jm function)
```

Variable-output shapes (event finder, peak detector) are a capability
flag on any output-producing preset, not their own preset:
`--variable-output --max-out N` with repeatable `--result-field name:T`.

Each preset is a CLI flag the wizard passes to `jm object` (or `jm function` for option 6). The presets bundle the right combination of
existing flags + a hand-tuned `_core.c` skeleton with concrete `/* TODO */` markers (see below).

Phase 3: **types and state.** Only the questions the preset needs:

- processor / blockwise — `arg_type`, `return_type`, state vars
- generator — `return_type`, state vars
- consumer — `arg_type`, state vars
- reader — init_params (filepath, mode, format), state vars (fd,
    file_size, position), custom method names (read, seek, close)
- function — function names, in/out param shapes (variable-output is
    `--variable-output --max-out N` + repeatable `--result-field`)

Phase 4: **implementations (optional).** For each generated `/* TODO */`
the wizard offers a "paste your C body (or skip)" prompt. Pasted bodies
flow through `--impl file::funcname` so the implementation lives where
the user expects it — in the generated `_core.c`, not in a TOML string.
Skipped bodies leave the `/* TODO */` marker for later editing.

Phase 5: **extras.** Only asked if relevant: perf hints (`JM_HOT`,
SIMD), external CMake deps (`find_packages`, `pkg_modules`,
`extra_link_libs`, `extra_include_dirs`), test framework choices. The
wizard runs `jm perf` and edits the manifest's `[project]` section
directly for the dep flags — still no TOML surface for the user.

After phase 5 the wizard prints:

```
Done!  Your project is ready at ./<name>/
Next step: open native/src/<name>/<name>_core.c and replace the
           /* TODO */ markers with your algorithm.

To build:    cd <name> && jm build
To test:     jm test
```

That's the whole experience. No `jm apply`, no fragment paths, no TOML
keys learned.

______________________________________________________________________

## What "great templates" means concretely

The generalists handle everything around the body. The presets are
what users *see* and *copy from* — they only land if the worked
`_core.c` body produced for each shape is good enough that the user
can fill in one `/* TODO */` and have a working component. The
current generalist render is close but shape-agnostic. The improvement
is to make the same render shape-aware: when `--arg-type` is `T[]`,
emit a block-loop body; when `--arg-type` is `void`, emit a generator
body; when `--return-type` is `void`, emit an accumulator; when
`--no-step` is set, emit no body at all. Same renderer, branching on
flag state.

Example bodies the shape-aware render should produce:

```c
/* processor */
JM_FORCEINLINE JM_HOT float _Complex
my_proc_step(my_proc_state_t *state, float _Complex x)
{
    /* TODO: replace this body with your per-sample math.
       state-> fields are declared in my_proc_core.h. */
    return state->gain * x;
}

/* blockwise */
void
my_xform_steps(my_xform_state_t *state,
               const float _Complex *in, size_t n,
               float _Complex *out)
{
    /* TODO: process n elements from in[] into out[]. */
    for (size_t i = 0; i < n; i++) {
        out[i] = state->gain * in[i];
    }
}

/* generator */
void
my_gen_steps(my_gen_state_t *state, float _Complex *out, size_t n)
{
    /* TODO: produce n values into out[]. */
    for (size_t i = 0; i < n; i++) {
        out[i] = /* advance state, emit value */ 0.0f + 0.0f * I;
    }
}

/* reader — auto-generated for jm object foo --reader --init-param filepath:"const char *" */
my_reader_state_t *
my_reader_create(const char *filepath)
{
    my_reader_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj) return NULL;

    /* TODO: open filepath, populate state. */
    obj->fd = open(filepath, O_RDONLY);
    if (obj->fd < 0) { free(obj); return NULL; }

    /* TODO: compute obj->file_size, obj->num_samples, etc. */
    return obj;
}

size_t
my_reader_read(my_reader_state_t *state, float _Complex *out, size_t n)
{
    /* TODO: read up to n values from state->fd into out[]; return count. */
    return 0;
}

/* variable-output capability (on any output-producing preset) */
size_t
my_obj_detect_max_out(my_obj_state_t *state)
{
    (void)state;
    return 1024;  /* TODO: tune to your worst-case event count per call. */
}

size_t
my_obj_detect(my_obj_state_t *state,
              const float _Complex *in, size_t n_in,
              record_t *out)
{
    /* TODO: scan in[0..n_in-1], emit records into out[],
       return count (<= my_obj_detect_max_out(state)). */
    return 0;
}
```

A skeleton with no behaviour at all isn't worth much; one that builds,
runs, and prints something — even just `out[i] = in[i] * state->gain;`
— gives the user something to swap into. The wizard's job is to deliver
*that* skeleton, matched to the chosen preset.

______________________________________________________________________

## CLI flags the wizard needs (and would be useful without it)

These are TOML-only patterns today; the proposal is to expose each as a
flag so the wizard never has to write TOML, and so power users get the
same expressiveness from the command line.

| Pattern (today in TOML)               | Proposed flag                                                                                                            |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `[[obj.init_params]]`                 | `--init-param name:type[:default]` on `jm object` (repeatable; mirrors `--state`)                                        |
| `[[obj.methods]] out_type = "..."`    | `--out-type T` on `jm method`                                                                                            |
| `[[fn.params]] out = true`            | `--out-param name:T[]` on `jm function` ✅ shipped in 0.13.22                                                            |
| `[[method.params]] out = true`        | `--out-param name:T[]` on `jm method` (parallel)                                                                         |
| `variable_output = true` + max_out fn | `--variable-output --max-out N` on `jm object`/`jm method` (already half-supported)                                      |
| `[project] find_packages`             | `--find-package NAME` on `jm new` (repeatable) — already partially supported                                             |
| `[module.X] extra_include_dirs`       | `--extra-include-dirs '${X_INCLUDE_DIR}'` on `jm module` and `jm object`                                                 |
| Reader preset                         | `--reader` on `jm object` — implies `--no-step --init-param filepath:"const char *"` and emits a reader-shaped `_core.c` |
| Generator preset                      | `--generator` on `jm object` — implies `--arg-type void` and emits a generator-shaped step()                             |
| Consumer preset                       | `--consumer` on `jm object` — implies `--return-type void` and emits a consumer-shaped step()                            |
| Blockwise preset                      | `--blockwise` on `jm object` — implies array `arg_type`/`return_type` and emits the loop                                 |

None of these break existing flag syntax. None require schema changes
to the TOML — every flag still rounds-trips into the same keys. Power
users keep editing TOML if they want; new users never see it.

______________________________________________________________________

## Implementation sketch

The wizard itself is one file: `src/just_makeit/_wizard.py`. ~400 LOC
for the prompts + dispatch.

- **Prompts.** Plain `input()` with explicit defaults. No prompt
    library dependency. Each prompt is a one-line function returning a
    typed answer; the dataclass `WizardState` accumulates them.
- **Dispatch.** Wizard calls into the existing CLI handlers
    (`_new.run`, `_object.run`, `_method.run`, etc.) directly. No shell
    invocation. Errors from the handlers bubble up to a graceful "Sorry,
    that failed — here's the error" message and a chance to re-answer.
- **Presets = named flag combinations on a shape-aware render.** Two
    generalists handle everything around the body. Each preset is a
    documented common flag combination that the CLI expands before the
    normal arg parser runs — `--blockwise` becomes
    `--arg-type "T[]" --return-type "T[]"`, `--generator` becomes
    `--arg-type void`, etc. The body emerges from the renderer being
    shape-aware: arg/return types and `--no-step` drive whether the
    body is a scalar step, block loop, generator loop, accumulator, or
    omitted. No per-preset template directory; the gallery page is the
    user-facing "template" they inspect or copy from.
- **CLI flag additions.** Land before the wizard, each in its own PR.
    Each comes with regression tests. Order of effort, cheapest first:
    `--init-param` (mirrors `--state`), `--out-type`, parallel
    `--out-param` for `jm method`, `--max-out`, then the four preset
    flags.

Testing the wizard end-to-end is straightforward: feed canned input
through `sys.stdin`, then assert on the generated project tree. Each
preset gets one test that runs the full wizard with default answers and
diffs the output against a golden tree.

______________________________________________________________________

## Acceptance — when is it done?

The wizard is shippable when:

1. Each of the seven presets, run with default answers, produces a
    project that `jm build && jm test` passes immediately — no TOML
    editing, no follow-up `jm apply`.
1. Re-running the wizard inside an existing project skips phase 1 and
    asks "add another component?" — routing to `jm object` / `jm method`
    / etc. without re-scaffolding the project.
1. Every TOML-only feature listed at the bottom of
    [decision-tree.md](../decision-tree.md) has either a CLI flag or a
    wizard question that produces it.

Everything beyond — undo, dry-run preview, JSON output for editor
integration, recovering from a half-finished session — is v2.
