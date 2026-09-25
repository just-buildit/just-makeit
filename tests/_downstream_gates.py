"""gh-1443 Gate A: hold a generated tree to the gates a downstream runs.

jm's examples prove a generated project WORKS: it scaffolds, builds, and
passes its own tests. doppler additionally requires it to be CLEAN and
STABLE, and every defect in five releases on 2026-09-21 lived in that
difference -- jm's CI green, doppler red on adoption. So, over every project
an example leaves behind:

1. ``jm status --check`` exits 0;
2. a second ``jm apply`` prints no ``warning`` line and writes no file (the
   latter trusts gh-1474's byte-derived report);
3. the project's Python is ``ruff check`` clean and ``ruff format`` stable,
   by jm's pinned ruff under the project's own configuration.

Registration-free twice over: `test_examples` hands every example to this,
and every project found under its root is checked, so neither a new example
nor a new generated file needs naming here.

What already fails is recorded per example in ``tests/gate_a/<name>.txt``
and may only SHRINK: a finding missing from the file fails, and so does a
file line that no longer happens -- delete it, which is the ratchet moving.
``JM_GATE_A_UPDATE=1`` rewrites the files from a run, for a reviewer to read
as a diff. One file per example so parallel workers never share one.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from _jmrun import run_cli

RATCHET_DIR = Path(__file__).parent / "gate_a"
UPDATE = os.environ.get("JM_GATE_A_UPDATE") == "1"
#: The shrink half -- "a recorded finding that no longer happens must be
#: deleted" -- runs where the ratchet was RECORDED: a Linux environment with
#: doppler, CI's `Examples (ubuntu-latest)` leg. Elsewhere a finding can vanish
#: because the environment differs, not because anything was fixed:
#: without doppler, kitchen_sink scaffolds no `tone`, and its `__init__.py`
#: -- which exists either way -- is then ruff-clean. "No NEW finding" runs
#: everywhere; that is the regression half.
SHRINK = os.environ.get("JM_GATE_A_SHRINK") == "1"

# A section header in `jm status` output: `STALE (2) — ...`.
_SECTION = re.compile(r"^([A-Z][A-Z-]+(?: [A-Z-]+)*) \(\d+\)", re.M)
_WRITE = re.compile(r"^\s+(create|update)\s+(\S+)", re.M)
# `path:row:col: CODE message`, from `--output-format=concise`.
_RUFF = re.compile(r"^(.+?):\d+:\d+: ([A-Z]+\d+)\b", re.M)
_REFORMAT = re.compile(r"^Would reformat: (.+)$", re.M)


def _ruff(proj: Path, *args: str) -> str:
    """jm's pinned ruff, run over *proj* under *proj*'s own config.

    ``--no-cache`` because a ``.ruff_cache`` left in the tree is a file
    nobody wrote, and the next `status` would be entitled to see it.
    """
    r = subprocess.run(
        [sys.executable, "-m", "ruff", *args, "--no-cache", "."],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # 0 is clean and 1 is findings; anything else is ruff not running, and
    # a ruff that did not run reports nothing -- which reads as clean.
    assert r.returncode in (0, 1) and "No module named" not in r.stderr, (
        f"Gate A could not run ruff ({r.returncode}): {r.stderr.strip()}\n"
        "It needs the project environment: `make test-examples`."
    )
    return r.stdout + r.stderr


def findings(root: Path) -> "set[str]":
    """Every Gate A finding for every jm project under *root*.

    Each is one line, prefixed with the project's path relative to *root*,
    and carries nothing that varies between runs -- no temp path, no line
    number, no count -- so a ratchet file can be compared exactly.
    """
    out: set = set()
    for manifest in sorted(root.rglob("just-makeit.toml")):
        proj = manifest.parent
        # A project nested in another's tree is its own project, checked on
        # its own; the outer walk must not count its files twice.
        tag = proj.relative_to(root).as_posix()

        def norm(text: str) -> str:
            text = text.replace(str(proj) + os.sep, "").replace(str(proj), ".")
            # jm prints paths the way the platform spells them; a ratchet
            # file is one file for every platform.
            return text.replace(os.sep, "/") if os.sep != "/" else text

        # `--diff` changes no section and no exit code; it is here so a new
        # finding's output shows WHAT differs, not only which file.
        st = run_cli("status", "--check", "--diff", cwd=proj)
        if st.returncode != 0:
            # gh-1619: kept, so a new status finding names its files in the
            # CI log rather than only its section.
            STATUS_OUT[tag] = st.stdout
            sections = _SECTION.findall(st.stdout) or ["(no section)"]
            for s in sections:
                out.add(f"{tag}\tstatus --check\t{s}")

        ap = run_cli("apply", cwd=proj)
        text = norm(ap.stdout + ap.stderr)
        if ap.returncode != 0:
            out.add(f"{tag}\tre-apply\texit {ap.returncode}")
        for line in text.splitlines():
            if line.lstrip().startswith("warning"):
                out.add(f"{tag}\tre-apply warns\t{' '.join(line.split())}")
        for verb, path in _WRITE.findall(text):
            out.add(f"{tag}\tre-apply writes\t{verb} {path.rstrip(':')}")

        for path, code in _RUFF.findall(
            norm(_ruff(proj, "check", "--output-format=concise"))
        ):
            out.add(f"{tag}\truff check\t{Path(path).as_posix()} {code}")
        for path in _REFORMAT.findall(norm(_ruff(proj, "format", "--check"))):
            out.add(f"{tag}\truff format\t{Path(path).as_posix()}")
    return out


_PATHISH = re.compile(r"[\w.-]+(?:/[\w.-]+)+|[\w-]+\.\w+")


#: `status --check`'s output per project from the last :func:`findings`,
#: printed when a status finding is new.
STATUS_OUT: "dict[str, str]" = {}


def _applies(finding: str, root: Path) -> bool:
    """Could *finding* have been observed in this run?

    Some projects and files exist only on some platforms or environments --
    kitchen_sink's ``tone`` is scaffolded only where a doppler build is
    available, and a module may declare ``platforms``. A ratchet line about
    a file this run never produced is not a finding that went away; it is
    one that could not be looked for. The shrink rule applies everywhere the
    file exists, which is where it can be checked.
    """
    tag, _kind, detail = finding.split("\t", 2)
    proj = root / tag
    if not proj.is_dir():
        return False
    detail = detail.replace("warning ~: ", "").replace("warning !: ", "")
    m = _PATHISH.search(detail)
    return m is None or (proj / m.group(0).rstrip(":")).exists()


def check(name: str, root: Path) -> None:
    """Compare *root*'s findings with example *name*'s ratchet file."""
    got = findings(root)
    ratchet = RATCHET_DIR / f"{name}.txt"
    if UPDATE:
        if got:
            RATCHET_DIR.mkdir(exist_ok=True)
            ratchet.write_text(
                "".join(f"{f}\n" for f in sorted(got)), encoding="utf-8"
            )
        elif ratchet.exists():
            ratchet.unlink()
        return
    allowed = (
        set(ratchet.read_text(encoding="utf-8").splitlines())
        if ratchet.exists()
        else set()
    )
    new = sorted(got - allowed)
    gone = (
        sorted(f for f in allowed - got if _applies(f, root)) if SHRINK else []
    )
    msg = []
    if new:
        msg.append(
            f"gh-1443 Gate A: example {name!r} fails a downstream gate it"
            " did not fail before:\n  " + "\n  ".join(new)
        )
        for tag in sorted(
            {f.split("\t", 1)[0] for f in new if "\tstatus --check\t" in f}
        ):
            msg.append(f"`jm status --check` in {tag}:\n{STATUS_OUT[tag]}")
    if gone:
        msg.append(
            f"gh-1443 Gate A: {ratchet.name} lists findings that no longer"
            " happen -- delete these lines (the ratchet only shrinks):\n  "
            + "\n  ".join(gone)
        )
    assert not msg, "\n\n".join(msg)
