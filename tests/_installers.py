"""jm's two installers, and the shell functions each defines (gh-1972).

jm ships two scripts that install the system packages a generated project
builds with:

- ``install.sh``, the curl installer the README runs. ``make docs`` copies
  it to the docs site's root, and it is fetched ALONE, before jm exists, so
  it cannot source anything else jm ships.
- ``src/just_makeit/scripts/install-deps.sh``, which the wheel ships as
  ``just-makeit install-deps`` and ``jm-install-deps``.

So each carries its own ``_install_<manager>()`` per package manager: two
copies by necessity. ``tests/test_gh1972_installers_patchelf.py`` holds the
two to one another, and ``tests/test_gh1897_bootstrap_bootstraps.py`` holds
the scaffolded ``bootstrap.toml`` to install-deps. Both read the functions
through `functions` here, so they cannot disagree about where one ends.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The curl installer, served from the docs site's root.
INSTALL_SH = ROOT / "install.sh"

#: The installer the wheel ships, behind ``just-makeit install-deps``.
INSTALL_DEPS = ROOT / "src" / "just_makeit" / "scripts" / "install-deps.sh"

#: Both, for a check that holds every installer to one rule.
INSTALLERS = (INSTALL_SH, INSTALL_DEPS)

#: What names a per-manager install function: ``_install_apt`` -> ``apt``.
INSTALL_PREFIX = "_install_"


def functions(script: Path) -> "dict[str, str]":
    """``{name: definition}`` for each function *script* defines at column 0.

    A function runs to the line where its braces balance, so a one-line body
    and a multi-line one are read alike; ``${MGR}`` is balanced on its own.
    The definition is the shell text itself, so a caller can hand it to
    ``bash`` and run it rather than read it.

    Parameters
    ----------
    script : Path
        A bash script.

    Returns
    -------
    dict[str, str]
        Each top-level function's name, mapped to its definition. A function
        defined inside another block (indented) is not top-level and is not
        returned.

    Examples
    --------
    >>> "_detect_mgr" in functions(INSTALL_SH)
    True
    >>> functions(INSTALL_SH)["_detect_mgr"].splitlines()[-1]
    '}'
    """
    lines = script.read_text(encoding="utf-8").splitlines()
    out: "dict[str, str]" = {}
    for i, line in enumerate(lines):
        m = re.match(r"(\w+)\(\)", line)
        if not m:
            continue
        depth, body = 0, []
        for ln in lines[i:]:
            body.append(ln)
            depth += ln.count("{") - ln.count("}")
            if depth == 0 and "{" in "".join(body):
                break
        out[m.group(1)] = "\n".join(body)
    return out


def install_functions(script: Path) -> "dict[str, str]":
    """``{manager: definition}`` for each ``_install_<manager>()`` in *script*.

    Examples
    --------
    >>> "apt" in install_functions(INSTALL_DEPS)
    True
    >>> install_functions(INSTALL_DEPS)["apt"].startswith("_install_apt()")
    True
    """
    return {
        name[len(INSTALL_PREFIX) :]: text
        for name, text in functions(script).items()
        if name.startswith(INSTALL_PREFIX)
    }
