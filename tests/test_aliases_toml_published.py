"""jbx's ``just-makeit`` namespace is answered by the published site.

``jbx just-makeit:NAME`` fetches ``<site>/aliases.toml`` first and only then
probes ``<site>/NAME.sh``. So the site must ship an ``aliases.toml`` whose
every target is a file the site ships; an alias to a path the publish step
does not produce is a 404 for every user of the namespace.

The artefact is read, not the source: this runs ``make site-files``, the
very recipe ``DOCS_BUILD_CMD`` runs after the zensical build to put
``install.sh`` and ``aliases.toml`` in ``site/``, into a temporary directory,
and reads what it wrote. It needs no zensical, so it runs on every leg of
the matrix. Registration-free: any alias added later is held to the same
rule with no edit here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from just_makeit import _config as C

ROOT = Path(__file__).resolve().parent.parent


def _site_url() -> str:
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    m = re.search(r"^site_url:\s*(\S+)\s*$", text, re.M)
    assert m, "mkdocs.yml declares no site_url"
    return m.group(1).rstrip("/")


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> Path:
    """The directory ``make site-files`` filled: the site's extra files."""
    out = tmp_path_factory.mktemp("site")
    r = subprocess.run(
        ["make", "-s", "site-files", f"SITE_FILES_DIR={out}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _aliases(site: Path) -> dict:
    """``[aliases]`` of the published ``aliases.toml``."""
    f = site / "aliases.toml"
    assert f.is_file(), "the publish recipe ships no aliases.toml"
    with f.open("rb") as fh:
        return C.tomllib.load(fh)["aliases"]


def test_install_deps_is_aliased(published):
    assert "install-deps" in _aliases(published)


def test_every_alias_targets_a_file_the_site_ships(published):
    base = _site_url()
    bad = []
    for name, url in sorted(_aliases(published).items()):
        if urlparse(url).scheme != "https" or not url.startswith(base + "/"):
            bad.append(f"{name}: {url!r} is not an https URL under {base}/")
            continue
        rel = url[len(base) + 1 :]
        if not rel or not (published / rel).is_file():
            bad.append(f"{name}: {url!r} -> {rel} is not shipped")
    assert not bad, "\n".join(bad)
