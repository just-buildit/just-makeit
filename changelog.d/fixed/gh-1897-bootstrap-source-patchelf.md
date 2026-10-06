- **A scaffolded `bootstrap.toml` bootstraps: its just-makeit source
    fetches, and every Linux dev group installs patchelf** (gh-1897).
    `[tools.just-makeit]` named `just-bashit:just-makeit`, which just-runit
    fetches from `jbs/just-makeit.sh`. `jbs/` holds only just-bashit's own
    scripts, so the fetch 404'd and `just-runit install` failed in every
    project `jm new` made. The source is now jm's own installer,
    `https://just-buildit.github.io/just-makeit/install.sh`, the one the
    README's `curl` line runs. No `[dev.apt]`, `[dev.pacman]`, `[dev.dnf]`
    or `[dev.zypper]` group named `patchelf`, which auditwheel needs to
    repair a Linux wheel, so a project provisioned with
    `jbx install-deps -g dev` failed `pip wheel .`. All four now list it,
    as jm's own `install-deps` already did. `bootstrap.toml` is
    create-only, so `jm status` reports an existing project's as OUTDATED;
    adopting it is your call.
