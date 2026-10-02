"""gh-1792: every apt-get jm ships, generates or runs bounds each request.

``-o Acquire::Retries=3`` retries a request that ERRORS. A connection that
stalls mid-transfer never errors, so the retries never start and the job sits
until ``timeout-minutes`` cancels it -- 29 minutes, on 2026-10-01, a
cancelled leg that turned ``CI passed`` red on a PR whose code had passed
everywhere else. ``Acquire::http::Timeout`` and ``Acquire::https::Timeout``
turn the stall into an error, which the retries then cover.

The walk is registration-free: every tracked file that can run a command --
shell scripts, workflows, Dockerfiles, makefiles, and every template jm
renders into a project -- is read, its continuation lines joined, its
comments dropped, and each command segment that invokes ``apt-get`` must
carry all three options. A new apt-get anywhere in that set is covered the
moment it is committed. Prose (``*.md``, docstrings) is not a command, and
``docker/Dockerfile.ci`` passes its options through a bash array that
``tests/test_ci_image.py`` already gates.

GATE: every apt-get invocation in a shipped script, a generated template,
      a workflow, a Dockerfile or a makefile carries Acquire::Retries,
      Acquire::http::Timeout and Acquire::https::Timeout, so a stalled
      mirror errors and is retried instead of hanging the job.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = (
    "Acquire::Retries=",
    "Acquire::http::Timeout=",
    "Acquire::https::Timeout=",
)

# Gated elsewhere, by a check that understands its option array.
EXCLUDED = {"docker/Dockerfile.ci"}

TEMPLATES = "src/just_makeit/templates/"


def _runs_commands(rel: str) -> bool:
    """Whether a tracked file is one whose lines are executed as commands."""
    if rel in EXCLUDED or rel.startswith("tests/"):
        return False
    name = rel.rsplit("/", 1)[-1]
    if rel.startswith(TEMPLATES):
        # Every template is rendered into a project; only prose is exempt.
        return not name.endswith(".md")
    return (
        name.endswith((".sh", ".yml", ".yaml", ".mk"))
        or name == "Makefile"
        or name.startswith("Dockerfile")
    )


def _files() -> "list[str]":
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [f for f in out.splitlines() if _runs_commands(f)]


# A segment invokes apt-get when apt-get is its command word: after any YAML
# list / folded-scalar marker, shell keyword, env assignment, sudo or
# `timeout N` prefix. `echo "apt-get update failed"` is not an invocation.
_INVOKES = re.compile(
    r"""^[-\s>|]*
        (?:(?:if|then|do|else|RUN|!)\s+)*
        (?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*
        (?:(?:sudo|\$SUDO|\$\{SUDO\}|timeout\s+\d+)\s+)*
        apt-get\b""",
    re.VERBOSE,
)


def _segments(text: str) -> "list[str]":
    """Each command segment of a file, continuations joined, no comments."""
    joined = re.sub(r"\\\n", " ", text)
    lines = [
        ln for ln in joined.splitlines() if not ln.lstrip().startswith("#")
    ]
    segs = []
    for ln in _fold(lines):
        segs += re.split(r"&&|\|\||;|\|", ln)
    return [s.strip() for s in segs]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _fold(lines: "list[str]") -> "list[str]":
    """Join each YAML folded scalar (``- >-``) into the one line it means.

    Its body is every following line indented deeper than the marker's
    line, so an apt-get split across them reads as one command.
    """
    out: "list[str]" = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if re.search(r"(?:^|\s)>-?\s*$", ln):
            body = []
            while i < len(lines) and (
                not lines[i].strip() or _indent(lines[i]) > _indent(ln)
            ):
                body.append(lines[i].strip())
                i += 1
            head = ln.rstrip()
            ln = head[: head.rfind(">")] + " ".join(body)
        out.append(ln)
    return out


def _apt_calls() -> "list[tuple[str, str]]":
    calls = []
    for rel in _files():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if "apt-get" not in text:
            continue
        calls += [(rel, s) for s in _segments(text) if _INVOKES.match(s)]
    return calls


def test_the_walk_finds_the_known_apt_sites():
    # An empty walk is not a pass: a filter that drifted would read every
    # site as absent. These are the files gh-1792 fixed.
    found = {rel for rel, _ in _apt_calls()}
    expected = {
        "install.sh",
        "src/just_makeit/scripts/install-deps.sh",
        "src/just_makeit/scripts/docker-e2e.sh",
        "src/just_makeit/templates/ci/woodpecker.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/artifact.yml",
        ".github/workflows/release.yml",
    }
    assert expected <= found, f"the walk missed {sorted(expected - found)}"


def test_every_apt_get_bounds_each_request():
    calls = _apt_calls()
    assert len(calls) >= 10, f"the walk must find the apt calls: {calls}"
    unbounded = [
        f"{rel}: {seg}"
        for rel, seg in calls
        if not all(opt in seg for opt in REQUIRED)
    ]
    assert unbounded == [], (
        "an apt-get with no per-request timeout hangs on a stalled mirror "
        "instead of erroring and retrying (gh-1792); pass "
        "-o Acquire::Retries=3 -o Acquire::http::Timeout=30 "
        "-o Acquire::https::Timeout=30 to:\n" + "\n".join(unbounded)
    )


def test_the_matcher_tells_an_invocation_from_a_mention():
    # The matcher is what makes the walk mean anything: prove both sides.
    for seg in (
        "apt-get update",
        "- apt-get install -y x",
        "sudo timeout 300 apt-get -o A=1 update",
        "$SUDO apt-get install -y x",
        "if sudo apt-get update",
        "DEBIAN_FRONTEND=noninteractive apt-get install -y x",
    ):
        assert _INVOKES.match(seg), seg
    for seg in ('echo "apt-get update failed"', "grep apt-get file"):
        assert not _INVOKES.match(seg), seg
