# `jm wizard` — design sketch

Status: **proposal, not implemented.** Companion to the user-facing
[decision tree](../decision-tree.md). This note captures what an
interactive wizard would do, why it's worth building, and what the
smallest useful version looks like.

______________________________________________________________________

## Motivation

The decision tree page covers the same ground as a wizard would. The
difference is the cost of being wrong:

- **Doc** → user reads it, picks a branch, types the command. If they
    pick wrong they discover it from compile errors or unexpected output
    and consult the tree again.
- **Wizard** → asks the same questions in order, knows which TOML keys
    are reachable from which command, and emits both (a) the CLI line
    that reproduces the answer and (b) the TOML fragment that captures
    decisions the CLI can't express. The user pastes either one; they
    can't get the combination wrong.

The wizard's real value isn't the prompts themselves — it's that it
**closes the gap between the CLI and the TOML-only features**. Half of
the most powerful knobs (`create_impl`, `out_type`, `result_fields`,
`variable_output`, `extra_include_dirs`, `init_params` with `optional`)
have no flag and are silently missed by new users. A wizard makes them
visible by simply asking.

______________________________________________________________________

## Shape

```
$ jm wizard
just-makeit wizard — answer a few questions; I'll print the command
and any TOML you need to paste.

? Do you have a just-makeit.toml in the current directory?  No
? Project name:                                              dsp_lib
? Add an object now?                                         Yes
? Object name:                                               engine
? Will the object share a .so with peers? (module)           No, standalone
? step() input shape:                                        block in / block out (array)
? step() output shape:                                       same shape as input
? Internal state:                                            gain (float, 1.0f), n_taps (size_t, 64)
? Constructor signature differs from internal state?         No
? Custom step() body (paste C, blank to skip):               return x * state->gain;
? Any extra methods? (variable output, fixed buffer, etc.)   Yes
?   Method name:                                             reset_to
?   arg_type / return_type / params:                         void / void / threshold:double
?   impl body:                                               state->gain = (float)threshold;
?   Another method?                                          No
? External CMake deps?                                       No
? Performance hints (JM_HOT / SIMD batch)?                   Yes
? Run jm-install-deps first?                                 No (already done)

──────────────────────────────────────────────────────────────────────
Run this:

    jm new dsp_lib \
        --object engine \
        --arg-type "float[]" \
        --return-type "float[]" \
        --state gain:float:1.0f \
        --state n_taps:size_t:64 \
        --perf

Then paste this fragment as objects/engine_methods.toml and run jm apply:

    [[engine.methods]]
    name = "reset_to"
    arg_type = "void"
    return_type = "void"
    params = [{name = "threshold", type = "double"}]
    impl = '''
    state->gain = (float)threshold;
    '''

Step body for engine_core.h (or pass via --impl file::fn at scaffold time):

    return x * state->gain;
──────────────────────────────────────────────────────────────────────
```

The split between "run this" and "paste this" is deliberate: anything
the CLI flags can express stays in the command (because the user might
re-run with edits); anything that has to go through TOML is emitted as
a fragment for `jm apply` (because that's the canonical path for those
features).

______________________________________________________________________

## State machine

The questions split into five phases, each gated on the previous
answers:

1. **Project gate.** If no `just-makeit.toml`, ask for project name and
    add `jm new <name>` to the script. Otherwise skip to phase 2.
1. **Unit-of-work.** What are you adding? Maps to one of:
    `object`, `module`+`object`, `function`, `method`, `property`,
    `add`, `app`. Each routes to its own follow-up sub-questionnaire.
1. **Shape.** Routed by phase 2:
    - object → step() input/output shapes, state, init_params, no_step,
        no_state, mutable, perf
    - method → arg/return types, params, variable_output, out_type,
        multi_output, result_fields
    - function → params, return type, inline, impl
    - app → target (c/console/pep723), entry object
1. **Implementation.** Optional paste of impl bodies (step, create,
    reset, destroy, method, function). Each becomes either an `--impl  file::fn` flag or an `impl = '''...'''` TOML entry. Empty answer
    means "leave the `<<IMPLEMENT>>` placeholder in place."
1. **Integration.** External deps (`find_packages`, `pkg_modules`,
    `c_deps`), per-component `extra_link_libs` / `extra_include_dirs`,
    `extra_types`. These are TOML-only; they always go to the fragment.

The wizard never *runs* anything — it only prints. The user copies the
output. That keeps the wizard side-effect free, easy to test (compare
emitted string against fixture), and trivial to demo.

______________________________________________________________________

## What the wizard emits

For every session, exactly three artefacts are printed (any of which may
be empty):

1. **Shell script.** All commands in order, suitable for `bash -e`.
1. **TOML fragment(s).** One per object, ready for `jm apply <path>`.
    Fragments are explicit because they're the things the user might
    want to commit verbatim (e.g. to `objects/engine.toml`).
1. **Implementation snippets.** C bodies for step / create / reset /
    destroy / method / function impls. Printed with a target path
    (`native/inc/<comp>/<comp>_core.h` and similar) so the user knows
    where to paste.

The wizard's invariant: *if you run the script and paste the fragment
and the snippets where they're labelled, you get a project that
matches your answers*.

______________________________________________________________________

## Implementation sketch

- **Prompt library.** `prompt_toolkit` is already a transitive dep via
    `ipython`; otherwise standard `input()` with explicit defaults works
    fine — no need to add a heavy TUI dependency. Each prompt is a
    one-line function returning a typed answer.
- **State.** Plain dataclass `WizardState` carrying every answer.
    Phase functions take and return the same dataclass; the final
    emitter walks it once.
- **Emitter.** One renderer per artefact (shell script, TOML fragment,
    impl bodies). Each is a pure function `WizardState → str`. Tests
    feed canned `WizardState` instances through the emitters and assert
    on the output.
- **Wiring.** New file `src/just_makeit/_wizard.py`; new CLI dispatch
    arm in `_cli.py`. ~600 LOC including tests is a reasonable target.

The wizard does *not* need to know every command's full flag surface.
It only needs the subset of flags that map cleanly to "I want X" answers
(the decision tree's lookup table is the source of truth). Flags
outside that subset stay TOML-only — printed in the fragment, never in
the shell script.

______________________________________________________________________

## Acceptance — when is it done?

The wizard is shippable when:

1. The bundled `dsp_toolkit`, `running_stats`, `fir_filter`, and
    `iqfile` examples can each be reconstructed from a single wizard
    session whose emitted script + fragment, run end-to-end on a clean
    `/tmp` dir, produces the same generated tree.
1. Re-running the wizard inside an existing project routes to the
    additive subcommand (`method` / `property` / `add` / `function`)
    rather than starting from scratch.
1. Every TOML-only feature listed at the bottom of
    [decision-tree.md](../decision-tree.md) has at least one question
    that can produce it.

Anything beyond that — multi-object sessions, undo, dry-run preview,
JSON output for editor integration — is a v2 concern.
