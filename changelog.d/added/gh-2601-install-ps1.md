- **jm installs on Windows from PowerShell, and `jbx just-makeit:install`
    is the recommended install everywhere** (gh-2601). `install.ps1`, the
    Windows twin of `install.sh`, is published beside it, and
    `aliases.toml`'s new `[aliases.ps1]` table points jbx's PowerShell
    side at it. From Windows PowerShell 5.1 or PowerShell 7 it finds a
    Python 3.9+ (`py`, then `python`, then `uv python find`), installs
    just-makeit into `%LOCALAPPDATA%\just-makeit\venv`, copies its
    launchers into `%USERPROFILE%\.local\bin` (never over a file it did not
    put there), puts both on the window's `PATH` without activating the
    venv, proves `jm --version` runs, and warns when clang-cl or cmake is
    missing. A re-run with jm current downloads nothing. It holds no `exit`
    and is safe under `irm | iex` and dot-sourced, which is how jbx runs
    it. Before this the Windows pages said `py -m pip install just-makeit`
    or `uv tool install just-makeit`, and neither put `jm` on `PATH` on a
    fresh machine. `install.sh` and `jm-install-deps` under Git Bash, MSYS2
    or Cygwin now stop, naming what to run instead, where they used to read
    the package manager as unknown and check no compiler. Run as a child --
    `jbx just-makeit:install`, `bash install.sh`, a pipe -- `install.sh`
    used to say "Venv activated" about a venv that died with the child; it
    now symlinks every console script just-makeit declares into
    `~/.local/bin`, never replacing a file it did not make, asks the jbx
    shell function to put that directory on the caller's `PATH`, and calls
    jm ready only when the caller will find it. Whenever the directory is
    not on `PATH`, both installers name `jbx update-path`. The install pages
    put `jbx just-makeit:install` above the methods they already listed and
    add the Windows one; the Visual Studio examples run it as their first
    step.
