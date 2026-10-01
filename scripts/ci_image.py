#!/usr/bin/env python3
"""jm's CI toolchain image (gh-1796): what goes in it, and whether the pin matches.

The Linux legs of ``ci.yml`` run in one image, ``docker/Dockerfile.ci``,
pinned by digest in ``.github/ci-image``. Every input to that image is
pinned in turn -- the base and uv by digest, every apt package by a dated
snapshot.ubuntu.com snapshot -- so the toolchain a PR is tested with changes
only when a commit says so. Before this, every leg ran ``apt-get`` against a
live mirror: the toolchain was whatever that mirror served that minute, and a
stalled mirror hung a leg for 29 minutes until ``timeout-minutes`` cancelled
it (gh-1792).

Subcommands, each one make target (``make help``):

``packages``
    The apt packages the image installs from the snapshot: bootstrap.toml's
    ``dev.apt`` group, the same declaration ``make install-deps-dev`` reads.
    The Dockerfile adds only what CI needs beyond that, in its own
    ``CI_EXTRA_PACKAGES``.

``inputs``
    A hash of everything the image is built from (the Dockerfile and the
    package list). ``make ci-image-build`` stamps it on the image as the
    ``org.just-buildit.ci-inputs`` label.

``check``
    Fails unless every platform of the pinned image carries the ``inputs``
    of this tree. So a PR that edits the Dockerfile or the dev group cannot
    merge still testing the OLD image: it must re-pin, which is one dispatch
    of ``ci-image.yml`` on its branch. Reads the registry, not a local
    image, so it needs network and fails without it -- an image that cannot
    be read has not been shown to match.

``refresh``
    Moves the three pins in the Dockerfile to today's: the ubuntu base
    digest, ``APT_SNAPSHOT``, and the uv digest. Each is replaced by the
    value it captures, and each anchor must match exactly once, so a
    reformatted Dockerfile fails loudly instead of being left half-moved.
    The weekly ``ci-image.yml`` runs this, then builds and re-pins.

``smoke IMAGE``
    Proves an image can do what the legs need: compile C with gcc and clang,
    configure with cmake, import numpy, run git and uv.

Examples
--------
Print the package list::

    python3 scripts/ci_image.py packages
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "Dockerfile.ci"
BOOTSTRAP = ROOT / "bootstrap.toml"
PIN = ROOT / ".github" / "ci-image"
LABEL = "org.just-buildit.ci-inputs"
PLATFORMS = ("linux/amd64", "linux/arm64")

BASE_REF = "ubuntu:24.04"
UV_REF = "ghcr.io/astral-sh/uv:latest"

_DIGEST = r"sha256:[0-9a-f]{64}"
# What `refresh` moves, as (name, pattern whose ONE group is the value).
# The base appears on two FROM lines (the certs stage and the final one), so
# it is the one anchor allowed two matches -- and must have exactly two.
ANCHORS = (
    ("base", re.compile(rf"^FROM ubuntu:24\.04@({_DIGEST})", re.M), 2),
    ("snapshot", re.compile(r"^ARG APT_SNAPSHOT=(\d{8}T\d{6}Z)$", re.M), 1),
    (
        "uv",
        re.compile(rf"^COPY --from=ghcr\.io/astral-sh/uv@({_DIGEST})", re.M),
        1,
    ),
)


def packages(bootstrap: Path = BOOTSTRAP) -> "list[str]":
    """Return bootstrap.toml's ``dev.apt`` packages, in declared order."""
    data = tomllib.loads(bootstrap.read_text(encoding="utf-8"))
    return list(data["dev"]["apt"]["packages"])


def inputs(dockerfile: Path = DOCKERFILE, bootstrap: Path = BOOTSTRAP) -> str:
    """Hash the Dockerfile and the package list into the image's label value.

    The package list is hashed, not bootstrap.toml: a change to another
    group (brew, pacman) builds the same image and must not demand a re-pin.
    """
    h = hashlib.sha256()
    h.update(dockerfile.read_bytes())
    h.update(b"\0" + " ".join(packages(bootstrap)).encode())
    return h.hexdigest()


def refresh(text: str, base: str, snapshot: str, uv: str) -> str:
    """Return the Dockerfile ``text`` with its three pins replaced.

    Raises ``ValueError`` naming the anchor when one does not match its
    required count, rather than writing a file that moved two pins of three.

    >>> t = ("FROM ubuntu:24.04@sha256:" + "a" * 64 + " AS certs\\n"
    ...      "FROM ubuntu:24.04@sha256:" + "a" * 64 + "\\n"
    ...      "ARG APT_SNAPSHOT=20260101T000000Z\\n"
    ...      "COPY --from=ghcr.io/astral-sh/uv@sha256:" + "b" * 64 + " /uv\\n")
    >>> out = refresh(t, "sha256:" + "c" * 64, "20261001T000000Z",
    ...               "sha256:" + "d" * 64)
    >>> out.count("c" * 64), "20261001T000000Z" in out, "d" * 64 in out
    (2, True, True)
    """
    new = {"base": base, "snapshot": snapshot, "uv": uv}
    for name, pat, want in ANCHORS:
        got = len(pat.findall(text))
        if got != want:
            raise ValueError(
                f"{DOCKERFILE.name}: the {name} pin matched {got} time(s), "
                f"expected {want}; fix the anchor in {Path(__file__).name}"
            )
        # Replace the captured value, not the line: the rest of each line is
        # the author's and stays byte-identical.
        text = pat.sub(
            lambda m, v=new[name]: m.group(0).replace(m.group(1), v), text
        )
    return text


def _digest(ref: str) -> str:
    """The registry's index digest for ``ref``, read with buildx."""
    out = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            "{{json .Manifest}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)["digest"]


def _labels(ref: str) -> "dict[str, dict[str, str]]":
    """Each platform's labels for the pinned index ``ref``."""
    out = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            "{{json .Image}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    images = json.loads(out)
    return {
        p: (images.get(p, {}).get("config", {}).get("Labels") or {})
        for p in PLATFORMS
        if p in images
    }


def check() -> int:
    """Exit status 0 when every platform of the pin carries this tree's inputs."""
    ref = PIN.read_text(encoding="utf-8").strip()
    if not re.fullmatch(rf"[\w./-]+@{_DIGEST}", ref):
        print(
            f"{PIN.relative_to(ROOT)}: not an image pinned by digest: {ref!r}"
        )
        return 1
    want = inputs()
    labels = _labels(ref)
    bad = [
        f"  {p}: {labels[p].get(LABEL, '(no label)')}"
        if p in labels
        else f"  {p}: (platform missing)"
        for p in PLATFORMS
        if labels.get(p, {}).get(LABEL) != want
    ]
    if bad:
        print(
            f"{ref}\nwas not built from this tree's docker/Dockerfile.ci and "
            f"bootstrap.toml dev.apt (inputs {want}):\n"
            + "\n".join(bad)
            + "\nRe-pin it: gh workflow run ci-image.yml --ref <this branch> "
            "-f refresh=false"
        )
        return 1
    print(f"{ref}: built from this tree ({', '.join(PLATFORMS)})")
    return 0


_SMOKE = r"""
set -euo pipefail
cd "$(mktemp -d)"
printf '#include <stdio.h>\nint main(void){puts("ok");return 0;}\n' > t.c
for cc in gcc clang; do $cc -Wall -Werror -o t-$cc t.c && ./t-$cc; done
printf 'cmake_minimum_required(VERSION 3.16)\nproject(t C)\n' > CMakeLists.txt
cmake -S . -B b >/dev/null && echo "cmake ok"
python3 -c 'import numpy; print("numpy", numpy.__version__)'
git --version
uv --version
"""


def smoke(image: str) -> int:
    """Run the smoke script in ``image``; its exit status is ours."""
    return subprocess.run(
        ["docker", "run", "--rm", image, "bash", "-c", _SMOKE]
    ).returncode


def main(argv: "list[str]") -> int:
    cmd = argv[0] if argv else ""
    if cmd == "packages":
        print(" ".join(packages()))
    elif cmd == "inputs":
        print(inputs())
    elif cmd == "check":
        return check()
    elif cmd == "refresh":
        snapshot = _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y%m%dT000000Z"
        )
        text = DOCKERFILE.read_text(encoding="utf-8")
        new = refresh(text, _digest(BASE_REF), snapshot, _digest(UV_REF))
        DOCKERFILE.write_text(new, encoding="utf-8")
        print(
            f"{DOCKERFILE.relative_to(ROOT)}: pins moved to {snapshot}"
            if new != text
            else "pins already current"
        )
    elif cmd == "smoke" and len(argv) == 2:
        return smoke(argv[1])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
