# Release checklist

## Before you start

- [ ] All intended changes are merged to `main`
- [ ] `uv run pytest` passes locally (947 passed as of v0.12.0)
- [ ] CI badge on `main` is green
- [ ] Run `python3 -c "from just_makeit._example import _EXAMPLES; print('\n'.join(_EXAMPLES))"` and visually confirm all examples pass locally — catches environment-sensitive failures (missing `pytest`, `cmake`, etc.) that CI may not surface

______________________________________________________________________

## 1. Decide the version number

| Change type                                  | Bump            |
| -------------------------------------------- | --------------- |
| Breaking CLI change (rename, remove command) | minor (`0.X.0`) |
| New command or flag                          | minor (`0.X.0`) |
| Bug fix, docs, internal refactor             | patch (`0.X.Y`) |

We are pre-1.0 so breaking changes bump minor, not major.

______________________________________________________________________

## 2. Update version and changelog

```sh
# pyproject.toml
version = "X.Y.Z"

# CHANGELOG.md — add a new section at the top:
## [X.Y.Z] — YYYY-MM-DD

### Breaking / Added / Fixed / Docs
- ...
```

Commit:

```sh
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump to X.Y.Z"
```

______________________________________________________________________

## 3. Push and wait for CI

```sh
git push origin main
```

Watch the [CI workflow](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml).
**Do not tag until CI is green.**

______________________________________________________________________

## 4. Tag and push the release tag

Tags must be prefixed with `v` — the Release workflow triggers on `v*`:

```sh
git tag vX.Y.Z
git push origin vX.Y.Z
```

This kicks off `release.yml`: test → build wheel → publish to PyPI.

______________________________________________________________________

## 5. Verify the release

Watch [release.yml](https://github.com/just-buildit/just-makeit/actions/workflows/release.yml)
complete all four jobs: `test`, `build`, `publish`, `github-release`.

The `github-release` job creates the GitHub Release automatically using the
relevant CHANGELOG section as the release notes — no manual step needed.

After publish, `artifact.yml` fires automatically and:

- Installs `just-makeit==X.Y.Z` from PyPI (retries for up to 10 min for CDN propagation)
- Scaffolds the `fir_filter` standalone workflow end-to-end (cmake build + test)
- Scaffolds the `filter_module` module/object workflow end-to-end
- Installs and verifies the C library via pkg-config and CMake `find_package`

If `artifact.yml` fails due to CDN lag it will auto-retry; if it fails for
any other reason, investigate before the next release.

______________________________________________________________________

## 6. Post-release

- [ ] Confirm `pip install just-makeit==X.Y.Z` works locally
- [ ] GitHub repo top-right shows the new version as "Latest release"
- [ ] Docs site rebuilt and live at https://just-buildit.github.io/just-makeit/

______________________________________________________________________

## Common pitfalls

| Mistake                                    | Fix                                                                                                                         |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Pushed tag without `v` prefix              | Push `vX.Y.Z` — release workflow ignores bare version tags                                                                  |
| Tagged before CI green                     | Delete the tag locally and on remote, fix CI, re-tag                                                                        |
| PyPI CDN lag causes `artifact.yml` to fail | Wait — retry loop runs for 10 min; if it still fails, check the logs                                                        |
| `artifact.yml` uses old CLI flags          | Keep `artifact.yml` in sync with any CLI renames                                                                            |
| Example `test.py` calls a tool not in the release environment | Guard optional tool invocations with an availability check (`import X`) and skip gracefully — see `full_workflow` step 7 as the pattern |
| GitHub repo still shows old version        | `github-release` job failed — check the Actions log and re-run, or create manually with `gh release create vX.Y.Z --latest` |

To delete a tag and re-tag:

```sh
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
# fix the issue, then:
git tag vX.Y.Z
git push origin vX.Y.Z
```
