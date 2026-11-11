"""No pacman partial upgrade anywhere jm ships or runs.

Arch supports only full upgrades. Refreshing the package databases without
upgrading (the ``y`` flag without ``u``) and then installing is a *partial
upgrade*: the new package may need libraries newer than the installed ones
it sits beside. It broke just-bashit's Arch CI on 2026-11-11, when openssl
4 would not install next to packages still linked to ``libcrypto.so.3``.
jm's two installers (``install.sh`` and ``install-deps.sh``) carried the
same line; both now run ``-Syu --noconfirm --needed``. ``-S`` alone (no
``y``) installs from the current databases and is not partial, so a doc
saying that is fine.

Registration-free: every tracked file is read (``git ls-files``), so a new
script, workflow or doc is covered the day it is added. The verdict is the
list of hits this test builds, never a tool's exit status: just-bashit's
first version of this check piped grep through xargs, which exits 123
whenever any file has no match, so it could not tell "found one" from
"found none" and never failed.

GATE: no tracked file runs pacman with a ``-S`` flag cluster holding ``y``
      but not ``u``.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from _installers import ROOT

#: ``pacman`` then a ``-S`` flag cluster (``-Sy``, ``-Syu``, ``-Syy``...).
_CLUSTER = re.compile(r"\bpacman\s+-(S[A-Za-z]*)")


def partial_upgrades(text: str) -> "list[int]":
    """Line numbers where *text* runs pacman with ``y`` and no ``u``.

    >>> partial_upgrades("pacman -S" + "y --noconfirm cmake\\n")
    [1]
    >>> partial_upgrades("pacman -Syu --noconfirm --needed cmake\\n")
    []
    """
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in _CLUSTER.finditer(line):
            flags = m.group(1)
            if "y" in flags and "u" not in flags:
                hits.append(n)
    return hits


# Built, not written: this file must not hold the line it refuses.
_PARTIAL = "pacman -S" + "y"


@pytest.mark.parametrize(
    "line, partial",
    [
        (f"sudo {_PARTIAL} --noconfirm cmake", True),
        (f"{_PARTIAL}y foo", True),  # -Syy: refresh twice, still partial
        ("sudo pacman -Syu --noconfirm --needed cmake", False),
        ("pacman -Syyu foo", False),
        ("pacman -S --needed foo", False),  # no refresh: not partial
        ("pacman -Qi foo", False),
    ],
)
def test_the_scan_tells_partial_from_full(line, partial):
    assert bool(partial_upgrades(line)) is partial, line


def _tracked() -> "list[str]":
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert len(out) > 100, "git ls-files listed too little to be the tree"
    return out


def test_no_tracked_file_runs_a_partial_upgrade():
    bad, read = [], 0
    for rel in _tracked():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # a binary: no command line to run
        read += 1
        bad += [f"{rel}:{n}" for n in partial_upgrades(text)]
    assert read > 100, read
    assert not bad, (
        "these run pacman refresh-without-upgrade, a partial upgrade Arch "
        "does not support; use -Syu --noconfirm --needed:\n  "
        + "\n  ".join(bad)
    )
