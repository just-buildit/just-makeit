- **A stalled apt mirror errors and retries instead of hanging the job**
    (gh-1792). `jm-install-deps`, the `install.sh` one-liner,
    `jm-docker-e2e` and the generated `.woodpecker.yml` (from
    `jm ci --provider woodpecker`) called `apt-get` with at most
    `-o Acquire::Retries=3`. Retries cover only a request that errors, and a
    connection that stalls mid-transfer never does, so one stalled mirror
    held a CI job until its `timeout-minutes` cancelled it (29 minutes on
    2026-10-01). Every `apt-get` jm ships or generates now also passes
    `-o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30`, on both
    `update` and `install`, so a stall becomes an error the retries cover.
    jm's own workflows do the same, and a test refuses any `apt-get` in a
    shipped script, template, workflow, Dockerfile or makefile without all
    three options. A project that already ran `jm ci` keeps its
    `.woodpecker.yml`; `--force` rewrites it from the template.
