- **A release's post-publish jobs no longer skip when the version is already
    on PyPI** (gh-2310). After a release published to PyPI and then failed at
    `github-release` or `docker`, **Re-run all jobs** re-ran `resolve`, which
    saw the version on PyPI and skipped `publish`; that skip cascaded through
    `needs` to `github-release` and `docker`, and the run ended green with the
    GitHub Release or the image missing. Observed on the dispatch of
    `release.yml` for the already-released `v0.111.2`: only `resolve` ran.
    `github-release` and `docker` now run when `publish` succeeded or was
    skipped only because the version is already published, and each is
    idempotent: `github-release` leaves an existing release as it is, and
    `docker` runs only when the commit's image tag is absent from GHCR (a new
    `image-state` job asks, and answers "absent" on any doubt). A failed
    `publish`, or a smoke that failed before it, still skips both. The
    gh-2260 test now holds that nothing that builds or publishes runs for a
    published version, and a new test requires any job that escapes the skip
    cascade with `always()`, `failure()` or `cancelled()` to say what a skipped
    `publish` means.
