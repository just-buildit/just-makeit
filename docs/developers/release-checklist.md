# Release checklist

The procedure lives in `standard.mk`'s targets, not here. This page owns the
two things they cannot know — **which digit to bump**, and **what to look at
when something goes wrong** — and names the target for everything else.

That split is the point. This page used to restate the mechanics: hand-edit
`pyproject.toml`, merge through a queue, expect the first `git commit` to
abort. All three had drifted from what the repo actually does, and a runbook
that disagrees with the tooling is worse than no runbook, because it is
followed.

______________________________________________________________________

## Before you start

- [ ] All intended changes are merged to `main`, and CI on `main` is green
- [ ] `make test` passes locally
- [ ] `make lint` passes locally
- [ ] `python3 -c "from just_makeit._example import _EXAMPLES; print('\n'.join(_EXAMPLES))"`
    — visually confirm the examples pass locally. This catches
    environment-sensitive failures (a missing `pytest`, `cmake`) that CI does
    not surface, because CI always has them

______________________________________________________________________

## 1. Decide the version number

Two questions, in order:

| Does the release...                                             | Bump            |
| --------------------------------------------------------------- | --------------- |
| add functionality -- a new command, flag, manifest key or shape | minor (`0.X.0`) |
| only fix things -- bugs, docs, internal refactors               | patch (`0.X.Y`) |

Read it off the changelog: any `Added` entry means minor. Pre-1.0
(SemVer 4, "anything MAY change at any time"), so whether the release breaks
something does not change the digit; a breaking change goes under
`### Breaking`, which becomes the release notes. `1.0.0` is a separate,
explicit decision.

## 2. Cut the release branch

```sh
make release-branch VERSION=X.Y.Z
```

Branches `chore/release-X.Y.Z` **off `origin/main`** — not off whatever HEAD
you happen to be on — and writes the version into every manifest a release
commit touches (`pyproject.toml`, `bootstrap.toml`, `uv.lock`) from one
declaration.

`make version-check` probes the two that declare a literal version
(`pyproject.toml`, `bootstrap.toml`), so a missed one is a red gate rather
than a number nobody reads. `uv.lock` is not probed and does not need to be:
`uv lock` regenerates it, and the `uv-lock` pre-commit hook fails on a lock
that has drifted from the manifest.

## 3. Review the promoted changelog

Nothing to write. Each PR added its entry as a file,
`changelog.d/<section>/<slug>.md`, rather than a line under
`## [Unreleased]` — so parallel PRs never conflict on `CHANGELOG.md`
(gh-1526). `release-branch` ran `make changelog-assemble VERSION=X.Y.Z`,
which moved every fragment into a new `## [X.Y.Z] — YYYY-MM-DD` section,
in section order, and deleted the fragments.

Read that section in `git diff`. **The release notes are extracted verbatim
from it**, so a fragment that reads badly is fixed here, in `CHANGELOG.md`.
`tag-release` refuses while any fragment is still unassembled
(`changelog-assembled-check`).

Writing a fragment: `changelog-check` fails a branch that changes
`src/just_makeit/` without one, and its message says where the file goes.
The sections are the directories under `changelog.d/`; `CHANGELOG_SECTIONS`
in `standard.mk` sets their published order.

## 4. PR it, and merge it green

```sh
git commit -am "chore: release vX.Y.Z"
git push -u origin HEAD
gh pr create --fill
```

`main` is protected: the ruleset requires a pull request, the status checks,
and linear history, and allows **rebase** merges only. Merge once every
required check is green — that merge is what makes the release safe, because
the tag will point at it.

## 5. Ship

```sh
git checkout main && git pull
make ship VERSION=X.Y.Z      # = tag-release + release-watch
```

`tag-release` refuses unless you are on `main`, local `main` equals
`origin/main`, and `version-check` agrees with the tag. It pushes **only the
tag**, never `main`, and it is idempotent — re-running `ship` after an
interrupted watch reuses a tag that already points at HEAD, and refuses one
that points anywhere else, because a released tag must not move.

`release-watch` streams `release.yml`'s jobs, auto-reruns **one** pre-publish
flake (safe: publish is gated behind smoke), and then verifies the real
artifacts — PyPI per-version and `latest`, and the GitHub Release. The
`github-release` job writes the release notes from the CHANGELOG section, so
there is no manual step.

After publish, `artifact.yml` fires automatically and installs
`just-makeit==X.Y.Z` from PyPI (retrying up to 10 min for CDN propagation),
scaffolds the `fir_filter` standalone and `filter_module` workflows end to
end, and verifies the C library via pkg-config and CMake `find_package`. If it
fails for anything other than CDN lag, investigate before the next release.

## 6. Post-release

- [ ] GitHub repo top-right shows the new version as "Latest release"
- [ ] Docs site rebuilt and live at
    <https://just-buildit.github.io/just-makeit/>

______________________________________________________________________

## When it goes wrong

Four of the old pitfalls are gone because `tag-release` refuses them: a tag
without the `v` prefix, a tag on a local commit that is not on `main`, a tag
while local `main` is behind, and a version mismatch between the tag and the
manifests. They are listed nowhere below because they can no longer happen.

| Mistake                                                                 | Fix                                                                                                         |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Want to "redo" a release after publish succeeded                        | You can't — PyPI rejects a duplicate version. Bump to the next patch and release that                       |
| PyPI CDN lag fails `artifact.yml`                                       | Wait — the retry loop runs for 10 min; if it still fails, read the logs                                     |
| `artifact.yml` uses old CLI flags                                       | Keep it in sync with any CLI rename                                                                         |
| An example's `test.py` calls a tool absent from the release environment | Guard optional tools with an availability check and skip gracefully — `full_workflow` step 7 is the pattern |
| GitHub still shows the old version                                      | The `github-release` job failed — read the Actions log and re-run, or `gh release create vX.Y.Z --latest`   |

**A release tag is immutable: a failed release burns its number.** The
repository's `release tags` ruleset refuses deleting or moving any `v*` tag,
with no bypass, so a release that fails -- even before `publish`, with nothing
on PyPI -- is not re-tagged. Fix the defect on `main`, then cut the next patch
through the normal bump PR, opening its CHANGELOG section with a line saying
the previous version was tagged but never published and pointing at its
section (0.90.1 is the worked example: 0.90.0 failed its pre-publish smoke
twice and was skipped). The unpublished section stays as written.

```sh
# fix merged on main, then:
make release-branch VERSION=X.Y.Z+1   # note the skipped version at the top
```
