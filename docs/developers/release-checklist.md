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

| Change type                                  | Bump            |
| -------------------------------------------- | --------------- |
| Breaking CLI change (rename, remove command) | minor (`0.X.0`) |
| New command or flag                          | patch (`0.X.Y`) |
| Bug fix, docs, internal refactor             | patch (`0.X.Y`) |

Pre-1.0, so the digits shift down one place: the minor digit stands in for
major (breaking changes only), and the patch digit absorbs both new features
and fixes.

## 2. Cut the release branch

```sh
make release-branch VERSION=X.Y.Z
```

Branches `chore/release-X.Y.Z` **off `origin/main`** — not off whatever HEAD
you happen to be on — and writes the version into every manifest a release
commit touches (`pyproject.toml`, `bootstrap.toml`, `uv.lock`) from one
declaration. `make version-check` reads the same table, so a missed file is a
red gate rather than a number nobody probes.

## 3. Promote the changelog heading

The only hand-written step, and it stays prose. Entries were already written
under `## [Unreleased]` by the PR that made each change — this promotes the
heading:

1. `## [Unreleased]` → `## [X.Y.Z] — YYYY-MM-DD`
1. add a fresh empty `## [Unreleased]` above it

Sections are Breaking / Added / Fixed / Docs, keep-a-changelog style. **The
release notes are extracted verbatim from this section**, so if a long-lived
branch left `[Unreleased]` lagging, populate it to match the shipped work now.

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

**Re-tagging is only safe before `release.yml` has published to PyPI.**
Re-pushing a tag re-triggers the workflow, and the upload step fails on a
duplicate version once `X.Y.Z` exists. If publish already succeeded, do not
re-tag — cut the next patch instead.

```sh
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
# fix the issue, then re-run the same command as before:
make ship VERSION=X.Y.Z
```
