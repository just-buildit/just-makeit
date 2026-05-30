# `jm wizard` — design sketch

Status: **proposal, not implemented.** Companion to the user-facing
[decision tree](../decision-tree.md) and the
[template gallery](../templates/index.md). This note captures what an
interactive wizard would do, why it's worth building, and what the
smallest useful version looks like.

The seven shape presets the wizard offers each have their own page in
the [template gallery](../templates/index.md), titled with the exact
CLI invocation that materialises them. Users can browse the actual
generated code before they pick a preset — no pseudocode, no guessing.

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
  1) filter         sample in → sample out                     (defaults)
  2) block          array in → array out                       (--block)
  3) source         no input; produces samples on demand       (--source)
  4) sink           consumes samples; no output                (--sink)
  5) reader         opens a file/socket; custom verbs          (--reader)
  6) detector       finds events in a stream; variable output  (--variable-output --max-out N)
  7) library        no class; just module-level functions      (--no-object)
```

Each preset is a CLI flag the wizard passes to `jm object` (or `jm function` for option 7). The presets bundle the right combination of
existing flags + a hand-tuned `_core.c` skeleton with concrete `/* TODO */` markers (see below).

Phase 3: **types and state.** Only the questions the preset needs:

- filter / block — `arg_type`, `return_type`, state vars
- source — `return_type`, state vars
- sink — `arg_type`, state vars
- reader — init_params (filepath, mode, format), state vars (fd,
    file_size, position), custom method names (read, seek, close)
- detector — `max_out`, threshold params, state vars
- library — function names, in/out param shapes

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

The presets only land if the `_core.c` skeleton the user sees after the
wizard exits is good enough that they can fill in one `/* TODO */` and
have a working component. The current templates are close but
shape-agnostic — they generate the same step() stub regardless of what
the user said they were building.

Per-preset skeletons:

```c
/* filter */
JM_FORCEINLINE JM_HOT float _Complex
my_filter_step(my_filter_state_t *state, float _Complex x)
{
    /* TODO: replace this body with your per-sample math.
       state-> fields are declared in my_filter_core.h. */
    return state->gain * x;
}

/* block */
void
my_xform_steps(my_xform_state_t *state,
               const float _Complex *in, size_t n,
               float _Complex *out)
{
    /* TODO: process n samples from in[] into out[]. */
    for (size_t i = 0; i < n; i++) {
        out[i] = state->gain * in[i];
    }
}

/* source */
void
my_nco_steps(my_nco_state_t *state, float _Complex *out, size_t n)
{
    /* TODO: produce n samples into out[]. */
    for (size_t i = 0; i < n; i++) {
        out[i] = /* advance state, emit sample */ 0.0f + 0.0f * I;
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
    /* TODO: read up to n samples from state->fd into out[]; return count. */
    return 0;
}

/* detector */
size_t
my_det_detect_max_out(my_det_state_t *state)
{
    (void)state;
    return 1024;  /* TODO: tune to your worst-case event count per call. */
}

size_t
my_det_detect(my_det_state_t *state,
              const float _Complex *in, size_t n_in,
              detection_t *out)
{
    /* TODO: scan in[0..n_in-1], emit event records into out[],
       return count (<= my_det_detect_max_out(state)). */
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
| Source preset                         | `--source` on `jm object` — implies `--arg-type void` and emits a generator-shaped step()                                |
| Sink preset                           | `--sink` on `jm object` — implies `--return-type void` and emits a sink-shaped step()                                    |
| Block preset                          | `--block` on `jm object` — implies array `arg_type`/`return_type` and emits the loop                                     |

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
- **Preset templates.** The per-preset `_core.c` skeletons live in
    `src/just_makeit/templates/c/src/presets/` (new directory). Each
    `<preset>_core.c.template` is rendered with the same `<<...>>`
    substitution as the existing templates. The CLI flag (`--reader`,
    `--block`, etc.) selects which template renders, plus its
    accompanying state/init_param defaults.
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
