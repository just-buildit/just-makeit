- **Sourcing the installer no longer closes the terminal** (gh-2582). The
    README's `. <(curl -fsSL .../install.sh)` runs the script in your own
    shell, and it turned on `set -euo pipefail` there: any failure (no
    Python, an unknown flag, `--check` finding work to do) exited the
    terminal, and a successful install left errexit and nounset behind, so
    the next failing command or a prompt hook reading an unset variable
    closed it later. It also left helpers such as `info`, shadowing GNU
    `info`. The install now runs in a subshell; only the venv activation
    touches your shell, a failure returns a status, and nothing else the
    script defined is left behind.
