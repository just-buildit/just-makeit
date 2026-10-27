"""jbx's ``just-makeit`` namespace is answered by the BUILT site.

``jbx just-makeit:NAME`` fetches ``<site>/aliases.toml`` first and only then
probes ``<site>/NAME.sh``. So the site must ship an ``aliases.toml`` whose
every target is a file the site ships; an alias to a path the build does not
produce is a 404 for every user of the namespace.

The artefact is read, not the source: this runs the real publish path
(``make docs``, which ends with the ``cp`` lines of ``DOCS_BUILD_CMD``) and
reads ``site/``. Registration-free: any alias added later is held to the same
rule with no edit here.
"""

from __future__ import annotations

import fcntl
import re
import subprocess
import tempfile

from pathlib import Path
from urllib.parse import urlparse

import pytest

from just_makeit import _config as C

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def _site_url() -> str:
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    m = re.search(r"^site_url:\s*(\S+)\s*$", text, re.M)
    assert m, "mkdocs.yml declares no site_url"
    return m.group(1).rstrip("/")


@pytest.fixture(scope="module")
def built_site() -> Path:
    """The ``site/`` the real publish path (``make docs``) just built."""
    # xdist workers share one ``site/``; the build's ``--clean`` wipes it.
    lock_path = Path(tempfile.gettempdir()) / "jm-aliases-site.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        r = subprocess.run(
            ["make", "-s", "docs"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    assert r.returncode == 0, r.stdout + r.stderr
    return SITE


def _aliases(site: Path) -> dict:
    """``[aliases]`` of the built site's ``aliases.toml``."""
    published = site / "aliases.toml"
    assert published.is_file(), "the built site ships no aliases.toml"
    with published.open("rb") as f:
        return C.tomllib.load(f)["aliases"]


def test_install_deps_is_aliased(built_site):
    assert "install-deps" in _aliases(built_site)


def test_every_alias_targets_a_file_the_site_ships(built_site):
    base = _site_url()
    bad = []
    for name, url in sorted(_aliases(built_site).items()):
        u = urlparse(url)
        if u.scheme != "https" or not url.startswith(base + "/"):
            bad.append(f"{name}: {url!r} is not an https URL under {base}/")
            continue
        rel = url[len(base) + 1 :]
        if not rel or not (built_site / rel).is_file():
            bad.append(f"{name}: {url!r} -> site/{rel} is not shipped")
    assert not bad, "\n".join(bad)
