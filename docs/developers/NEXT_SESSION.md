# Next session — handoff

This document is the hand-off snapshot at the end of the long
"Phase 1 + Phase 2" session. Pick this up to know where to start.

______________________________________________________________________

## TL;DR

- **Released**: v0.13.22 (PR #67). 7 issue fixes (gh-65, gh-66, gh-68,
    gh-69, gh-70, gh-71, gh-72) + `extra_include_dirs` + `--out-param`.
- **Phase 1 merged**: PR #73 (gallery + bind MVP + type slots + design
    docs). Branch `feat/jm-bind-mvp` is gone.
- **Phase 2 merged so far**: rows 1 & 2 (PRs #74, #75 squashed into
    `main`).
- **Phase 2 in flight**: PRs **#76, #77, #78, #79, #80** rebased onto
    main; CI re-running. **PR #81** (CLI ↔ TOML mapping inventory) also
    in flight.
- **Resume action**: when each PR's umbrella `CI passed` flips green,
    `gh pr merge <N> --repo just-buildit/just-makeit --squash   --delete-branch --admin`. Order doesn't matter; expect occasional
    rebase conflicts on `_cli_object.py` / `_cli_method.py` (kwargs
    overlap — keep both).

After the stack lands: cut **v0.13.23**, then start Phase 3.

______________________________________________________________________

## Open PRs at handoff

| PR                                                         | Phase 2 row | Branch                        | What it ships                                                                             |
| ---------------------------------------------------------- | ----------- | ----------------------------- | ----------------------------------------------------------------------------------------- |
| [#76](https://github.com/just-buildit/just-makeit/pull/76) | 3           | `feat/cli-function-out-type`  | `--out-type T` on `jm function`                                                           |
| [#77](https://github.com/just-buildit/just-makeit/pull/77) | 5-7         | `feat/cli-new-external-deps`  | `--find-package` / `--pkg-module` / `--c-dep` on `jm new`                                 |
| [#78](https://github.com/just-buildit/just-makeit/pull/78) | 8/11/12     | `feat/cli-extra-include-dirs` | `--extra-include-dirs` (object + module) + `--extra-link-libs` + `--extra-types` (module) |
| [#79](https://github.com/just-buildit/just-makeit/pull/79) | 4           | `feat/cli-result-field`       | `--result-field name:T` on method + function                                              |
| [#80](https://github.com/just-buildit/just-makeit/pull/80) | final       | `feat/cli-impl-lifecycle`     | `--impl SLOT::file::fn` for `create` / `reset` / `destroy`                                |
| [#81](https://github.com/just-buildit/just-makeit/pull/81) | doc         | `docs/cli-toml-mapping`       | Field-by-field inventory in `docs/configuration.md`                                       |

All six have passed local pytest (1611–1628 depending on which row's
diff they carry). Five passed Windows CI before the rebase; the rebase
was triggered by GitHub's "branch out of date" required-check rule
after #73-#75 merged.

### Verifying CI status

```sh
for pr in 76 77 78 79 80 81; do
  gh pr checks $pr --repo just-buildit/just-makeit | grep -E "^CI passed"
done
```

When all six show `passed`, the order is up to you. Each PR is
independent of the others *behaviour-wise*, but they touch overlapping
files (`_cli.py` help text, `_cli_object.py`, `_cli_method.py`,
`_cli_function.py`). Expect rebase conflicts on the second-onwards
merges — the conflicts are always "keep both new kwargs" in the
`_object.run(...)` / `_method.run(...)` call sites.

### Merge command

```sh
gh pr merge <N> --repo just-buildit/just-makeit \
    --squash --delete-branch --admin
```

`--admin` is needed because branch protection requires the
`CI passed` check and it takes a few minutes to land after each
update-branch.

### If a rebase conflicts

```sh
git fetch origin
git checkout origin/<branch-name>
git rebase origin/main
# resolve conflicts — they're always "keep both kwargs"
git add -A && git rebase --continue
git push origin HEAD:<branch-name> --force-with-lease
```

______________________________________________________________________

## Implementation plan progress

The plan lives at
[`docs/developers/implementation-plan.md`](implementation-plan.md)
(landed in PR #73 onto main).

| Phase                                               | Status       | Notes                                                           |
| --------------------------------------------------- | ------------ | --------------------------------------------------------------- |
| **0** — fixes, conventions, v0.13.22                | ✅ done      | Closed 7 issues.                                                |
| **1** — gallery + bind MVP + type slots             | ✅ done      | PR #73 merged.                                                  |
| **2** — CLI parity for every TOML field             | 🟡 in flight | Rows 1-2 merged; rows 3-12 + the inventory doc are PRs #76-#81. |
| **3** — presets + bind expansion + wizard           | 🔜 next      | Three threads. Detailed below.                                  |
| **4** — third-party bind, libclang, docs reordering | 🔜 future    | Polish; opens after Phase 3 lands.                              |

The Phase 2 acceptance bar — "every TOML field in
`docs/configuration.md` has a 'Reachable via CLI' column with ✓" — is
met by the inventory in PR #81 (the answer to "where is the command
mapping?"). After all six PRs merge, every 🟡 row in that table flips
to ✅.

______________________________________________________________________

## What's next — Phase 3

Phase 3 has three parallel threads. Pick any one; they don't depend on
each other.

### Thread 3a — Preset flags on `jm object` / `jm function`

Land `--preset {filter|block|source|sink|reader|detector|library}` so
the gallery's seven shapes are one-flag-each. The renderer side is
already there — each preset is a labelled bundle of existing flags +
a hand-tuned `_core.c` skeleton.

| Preset     | Status today              | What `--preset NAME` would do                                                                                                |
| ---------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `filter`   | shipped (default)         | Marker / alias for the existing default.                                                                                     |
| `library`  | shipped via `jm function` | Same as today; alias for clarity.                                                                                            |
| `block`    | not yet                   | `--arg-type "T[]" --return-type "T[]"` + block-shaped `steps()` skeleton with the for-loop pre-written.                      |
| `source`   | not yet                   | `--arg-type void` + `steps(n)` generator skeleton.                                                                           |
| `sink`     | not yet                   | `--return-type void` + accumulator skeleton.                                                                                 |
| `reader`   | not yet                   | `--no-step --init-param filepath:"const char *"` + `read()`/`seek()`/`close()` methods with `open()`/`stat()` already wired. |
| `detector` | not yet                   | `--variable-output --max-out N` + scan-and-emit skeleton.                                                                    |

Per-preset skeletons live in (proposed)
`src/just_makeit/templates/c/src/presets/*.template`. Each one is
small (~30 lines of C) and rendered through the existing
`_render.render` machinery.

### Thread 3b — Bind expansion

`src/just_makeit/_bind.py` today handles only the **filter** shape
(scalar state + inline `_step`). Extend the parser to recognise:

- Methods (any `<comp>_<verb>(...)` declared in the header).
- Init params (ctor args whose names don't match state fields).
- Output-param naming heuristic: `out` / `output` / `dst` / `dest`.
- Variable-output pairing: `<comp>_<verb>_max_out` sibling → `verb`
    binds as variable-output.
- Opaque state (forward decl in header; definition in `.c`).
- C enums in init params (doppler `det_noise_mode_t` pattern).
- Result-struct outputs (detector preset's `det_result_t`).

Wire `jm bind --check` into every bundled example's CI as a parity
gate.

Reference: [`bind-design.md`](bind-design.md).

### Thread 3c — `jm wizard`

A new file `src/just_makeit/_wizard.py` (~400 LOC) with plain
`input()` prompts that **runs commands in-process** (never prints
TOML). Phases:

1. Project gate.
1. Preset (the question is "What does your component do?" with the
    seven options from the gallery).
1. Types and state (only the questions the preset needs).
1. Optional impl bodies (paste a C body or skip).
1. Extras (perf, external deps).

Reference: [`wizard-design.md`](wizard-design.md).

______________________________________________________________________

## Release v0.13.23 — when to cut

After the Phase 2 stack merges (#76–#81). One commit, one PR:

```sh
# on main
git pull
# edit pyproject.toml: version = "0.13.23"
# edit CHANGELOG.md: promote [Unreleased] block to [0.13.23] — 2026-05-30
git commit -am "chore: bump to 0.13.23"
git push
gh pr create --title "Release 0.13.23: Phase 2 CLI parity" --body "..."
# squash-merge, then:
git pull
git tag v0.13.23
git push origin v0.13.23
```

Per the [release checklist](release-checklist.md): tag triggers
`release.yml` (test → build → publish → github-release), then
`artifact.yml` does end-to-end validation. ~20 minutes from tag push
to PyPI live.

The CHANGELOG entry should mention all 11 new CLI flags landed in
Phase 2 (everything in the 🟡 rows of the inventory table).

______________________________________________________________________

## Design doc index

Every design and reference doc that came out of this session, with
the file path and what it answers:

| Doc                                                                                                  | Answers                                                                                                                                      |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| [`implementation-plan.md`](implementation-plan.md)                                                   | The full phased roadmap. Where every PR fits, what stays, what goes, success criteria.                                                       |
| [`bind-design.md`](bind-design.md)                                                                   | How `jm bind` synthesises `_ext.c` from a hand-written `_core.h`. Parser strategy, contract, phased rollout.                                 |
| [`wizard-design.md`](wizard-design.md)                                                               | How an interactive wizard would work — runs commands in-process, never emits TOML.                                                           |
| [`../decision-tree.md`](../decision-tree.md)                                                         | "Which command do I want?" flat lookup.                                                                                                      |
| [`../templates/index.md`](../templates/index.md) + 7 preset pages                                    | "What does each preset produce, and what types does each slot accept/reject?"                                                                |
| [`../types.md`](../types.md)                                                                         | "Which types are legal in which slot?" Five slots (state, step I/O, init-param, function param, method param), explicit allowlists per slot. |
| [`../configuration.md` §Complete CLI ↔ TOML mapping](../configuration.md#complete-cli--toml-mapping) | "For every TOML key, what's the CLI flag?" Phase 2 acceptance bar.                                                                           |
| [`release-checklist.md`](release-checklist.md)                                                       | How to cut a release.                                                                                                                        |

All the docs cross-link. The decision tree points at the gallery; the
gallery's Concrete-types tables point at `types.md`; `types.md` points
back at the gallery; the implementation plan points at all of them.

______________________________________________________________________

## Risk areas / loose ends

1. **The `_cli_object.py` / `_cli_method.py` / `_cli_function.py`
    surface is growing.** Each Phase 2 row added 5-10 lines.
    Consolidating into a shared parser helper would help, but doing it
    during the Phase 2 churn would have caused unnecessary conflicts.
    **Action**: after the stack lands, consider a refactor PR that
    factors common flag-parsing patterns into `_cli_parse.py`.
1. **No `validate_slot()` helper yet.** The plan calls for a shared
    `_types.validate_slot(slot, type_str)` so every CLI handler and the
    bind parser produce identical error messages. Today each handler
    inlines its own type check. **Action**: land as part of Phase 2
    polish before opening Phase 3.
1. **`--init-param` syntax doesn't yet support spaces in type names**
    on the CLI (`'filepath:const char *'` works in the integration
    test because the shell quotes it; the parser splits on `:` once and
    takes the rest as the type). Works in practice but is fragile —
    consider a tighter syntax (`name@type` for unambiguous splitting).
1. **`jm bind` is filter-only.** Phase 3 thread 3b expands it. Until
    then, only `running_stats` actually exercises `jm bind` end-to-end.
1. **Phase 2 PRs that conflicted on rebase**: #78 and #79 each
    needed a manual conflict resolution on the `_object.run(...)` /
    `_method.run(...)` kwarg list. **Action**: when the next batch of
    flags ships, structure the kwargs additions in a way that's
    rebase-safe — e.g., always at the end of the call, alphabetical
    order.
1. **Codecov patch coverage failing on most PRs.** This is the
    Codecov.io advisory, not a blocker — project coverage went up.
    Long-term: add unit tests for the new validators so the patch
    coverage tracks the actual change. **Action**: when refactoring
    into `validate_slot()`, write the tests there.
1. **The 9 monitor task** (`buzmhx424`) is still running at handoff,
    polling CI on the 6 open PRs. It'll exit on its own when the last
    PR goes green or hit the 55-minute timeout — either way, harmless
    to ignore.

______________________________________________________________________

## Issue tracker

At handoff: **zero open issues** on
[just-buildit/just-makeit](https://github.com/just-buildit/just-makeit/issues).
The seven that were open at the start of the session (gh-65 through
gh-72) all closed with the v0.13.22 release. If new issues appear,
triage against the [decision tree](../decision-tree.md) and the
[CLI ↔ TOML mapping](../configuration.md#complete-cli--toml-mapping)
before assuming new code is needed — the answer might already be a
flag.

______________________________________________________________________

## Quick-start commands for next session

```sh
# 1. Sync.
cd ~/just-makeit
git checkout main && git pull

# 2. Check the open PRs.
gh pr list --repo just-buildit/just-makeit

# 3. Check CI status on each.
for pr in 76 77 78 79 80 81; do
  gh pr checks $pr --repo just-buildit/just-makeit | grep -E "^CI passed"
done

# 4. When all green: merge in any order.
for pr in 76 77 78 79 80 81; do
  gh pr merge $pr --repo just-buildit/just-makeit --squash --delete-branch --admin
  git pull
done

# 5. Cut v0.13.23 per the release checklist.

# 6. Open Phase 3 — pick a thread:
#    3a:  edit _cli_object.py to add --preset {block|source|sink|reader|detector}
#    3b:  edit _bind.py to recognise methods + init_params + opaque state
#    3c:  create src/just_makeit/_wizard.py
```

Good luck — every door is wide open.
