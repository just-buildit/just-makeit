- **`jbx just-makeit:install-deps` leaves `jm` on your PATH.** `jbx` runs
    install.sh sourced into its own subshell, so the venv it activated was
    gone when `jbx` returned: the install succeeded and `jm` was still not
    found. Run by `jbx`'s shell function, install.sh now writes the venv's
    `bin` to `jbx`'s reload marker (`JB_RELOAD_MARKER`), which puts it first
    on the calling shell's PATH. Sourcing install.sh directly activates the
    venv as before, and the default venv is still `/tmp/jm-venv`; through
    `jbx`, pass another after `--`:
    `jbx just-makeit:install-deps -- ~/my-venv`.
