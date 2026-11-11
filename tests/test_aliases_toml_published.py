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

The same goes for what the pages send a reader to (gh-2601): every
``jbx just-makeit:NAME`` they show must resolve -- ``NAME.ps1`` where it is
shown in PowerShell, ``NAME.sh`` (or an alias) elsewhere -- and every
site-root file they link (``install.sh``, ``install.ps1``) must be shipped,
as the repo's own copy.
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


def _reader_text() -> "list[Path]":
    """Where a reader is told to fetch a file from the site.

    The README, the hand-written docs (``docs/examples/`` is generated from
    the examples' steps, and absent until a docs build) and every example's
    ``.steps/``.
    """
    docs = ROOT / "docs"
    return (
        [ROOT / "README.md"]
        + sorted(
            p
            for p in docs.rglob("*.md")
            if p.relative_to(docs).parts[0] != "examples"
        )
        + sorted(
            (ROOT / "src" / "just_makeit" / "examples").glob("*/.steps/*")
        )
    )


def _site_files_named() -> "dict[str, list[str]]":
    """``{file: [where]}`` for every site-root file a reader is sent to.

    A page link has no extension and is the zensical build's; a link to a
    script or a TOML file is one ``make site-files`` must ship.
    """
    url = re.compile(re.escape(_site_url()) + r"/([\w.-]+\.(?:sh|ps1|toml))\b")
    named: "dict[str, list[str]]" = {}
    for path in _reader_text():
        for m in url.finditer(path.read_text(encoding="utf-8")):
            named.setdefault(m.group(1), []).append(
                path.relative_to(ROOT).as_posix()
            )
    return named


_JBX_NAME = re.compile(r"\bjbx just-makeit:([\w-]+)")
_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)


def _jbx_names() -> "dict[str, set[str]]":
    """``{NAME: {suffix}}`` for every ``jbx just-makeit:NAME`` a reader runs.

    jbx resolves the name through ``aliases.toml``, then probes the site
    for ``NAME.sh``; from PowerShell it runs ``NAME.ps1`` (gh-2601). So a
    name in a ``powershell`` fence needs the ``.ps1``, anywhere else the
    ``.sh``.
    """
    want: "dict[str, set[str]]" = {}
    for path in _reader_text():
        for lang, body in _FENCE.findall(path.read_text(encoding="utf-8")):
            ext = ".ps1" if lang == "powershell" else ".sh"
            for name in _JBX_NAME.findall(body):
                want.setdefault(name, set()).add(ext)
    return want


def test_every_jbx_just_makeit_name_resolves(published):
    """``jbx just-makeit:install`` on both sides, and any name added later:
    the published site answers each, in each shell it is shown in."""
    want = _jbx_names()
    assert want.get("install") == {".sh", ".ps1"}, want
    aliases = _aliases(published)
    bad = [
        f"jbx just-makeit:{name} needs {name}{ext}, which is not shipped"
        for name, exts in sorted(want.items())
        for ext in sorted(exts)
        if not (published / f"{name}{ext}").is_file()
        and not (ext == ".sh" and name in aliases)
    ]
    assert not bad, "\n".join(bad)


def test_every_file_a_reader_fetches_is_shipped(published):
    """``install.sh``, ``install.ps1`` (gh-2601), and any file named later:
    each is shipped, and is the repo's own copy byte for byte."""
    named = _site_files_named()
    assert {"install.sh", "install.ps1"} <= set(named), named
    bad = []
    for name, where in sorted(named.items()):
        shipped = published / name
        if not shipped.is_file():
            bad.append(f"{name} (named in {where[0]}) is not shipped")
        elif shipped.read_bytes() != (ROOT / name).read_bytes():
            bad.append(f"{name} is shipped, but is not the repo's {name}")
    assert not bad, "\n".join(bad)
