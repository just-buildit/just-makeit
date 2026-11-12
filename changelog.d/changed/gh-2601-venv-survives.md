- **The installers' venv survives a reboot, and a project root gets its own**
    (gh-2601). `install.sh` and `jm-install-deps` put their venv in
    `/tmp/jm-venv` by default, and `install.sh` then linked
    `~/.local/bin/jm` into it -- so where `/tmp` is a tmpfs or is cleaned at
    boot (Arch, Fedora, ...), every new shell after a reboot found a
    dangling `jm`. Each installer now decides where the venv goes in one
    function, by one rule: run in a project's root (the directory holds
    `pyproject.toml` or `just-makeit.toml`), the project's `./.venv`, with
    nothing linked into `~/.local/bin` and, under jbx, that venv's `bin` put
    on the caller's `PATH`; anywhere else, a per-user venv under
    `${XDG_DATA_HOME:-~/.local/share}/just-makeit/` (`install.ps1`: under
    `%LOCALAPPDATA%`), linked into `~/.local/bin` as before. A path
    argument, or `JM_VENV`, still wins, and counts as a project's when the
    directory holding it is a project root. An existing `/tmp/jm-venv` is
    left alone; a re-run replaces a `~/.local/bin` link into a venv that no
    longer exists, which is the way back after a reboot. Also: a relative
    link into a jm venv is no longer refused as foreign; an unwritable jbx
    reload marker is named instead of reporting jm ready; an exported
    `CDPATH` no longer corrupts the venv path. `install.ps1` no longer puts
    the venv's `Scripts` folder (its `python.exe`, `pip.exe`) on `PATH`,
    keeps a working install when the package index cannot be asked,
    installs a wheel rebuilt at the same version, matches a relative or
    `/`-separated `JM_VENV` / `JM_BIN_DIR`, and names a folder where a
    launcher would go instead of failing on it.
