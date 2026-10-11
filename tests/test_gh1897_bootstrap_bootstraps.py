"""gh-1897: the ``bootstrap.toml`` jm scaffolds can bootstrap its project.

``jm new`` writes a ``bootstrap.toml``. ``just-runit install`` (jbx) fetches
every ``source`` under its ``[tools.*]`` tables, and
``jbx install-deps -g dev`` installs its ``[dev.<manager>]`` packages. Both
halves were broken, so each has a gate here.

1. ``[tools.just-makeit]`` named ``just-bashit:just-makeit``. just-runit maps
   ``just-bashit:NAME`` straight to ``<jbs>/NAME.sh``, consulting no
   ``aliases.toml``, and ``jbs/`` mirrors just-bashit's own scripts, which jm
   is not one of. The fetch 404'd, so ``just-runit install`` failed in every
   scaffolded project. The source is now jm's own installer: the one the
   README's curl line runs, served from the docs site root.

2. No Linux dev group named ``patchelf``. auditwheel needs it to repair a
   Linux wheel, so a project provisioned from its own manifest could not
   build one. jm's ``install-deps`` installs patchelf for every Linux
   package manager it knows, and the template now lists it wherever that
   script does.

GATE: in a scaffolded ``bootstrap.toml``, every ``[tools.*]`` source fetches
    a script from where just-runit resolves it (network: a blip is retried,
    a 404 fails at once), the ``just-makeit`` source is jm's installer, and
    every ``[dev.<manager>]`` group names patchelf wherever jm's own
    ``install-deps`` installs it for that manager.
"""

from __future__ import annotations

import importlib.util
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from _installers import INSTALL_DEPS, install_functions
from _jmrun import run_cli
from just_makeit import _config as C

ROOT = Path(__file__).resolve().parent.parent
NCO_TONE = ROOT / "src" / "just_makeit" / "examples" / "nco_tone" / "test.py"

#: just-runit's ``_JBS_BASE``. A ``just-bashit:NAME`` source is fetched from
#: ``<base>/NAME.sh`` (``_resolve`` returns the ``jbs:`` marker and
#: ``_acquire_jbs`` fetches it), with no ``aliases.toml`` lookup on the way.
JBS_BASE = "https://just-buildit.github.io/jbs"


@pytest.fixture(scope="module")
def bootstrap(tmp_path_factory) -> dict:
    """The ``bootstrap.toml`` of the issue's repro, ``jm new p --object g``."""
    where = tmp_path_factory.mktemp("gh1897")
    r = run_cli("new", "p", str(where / "p"), "--object", "g", cwd=where)
    assert r.returncode == 0, r.stderr
    with (where / "p" / "bootstrap.toml").open("rb") as f:
        return C.tomllib.load(f)


def _installer_url() -> str:
    """Where jm's ``install.sh`` is served: the docs site's root.

    ``make docs`` copies ``install.sh`` into ``site/`` (the Makefile's
    ``DOCS_BUILD_CMD``), and the site is published at ``mkdocs.yml``'s
    ``site_url``. The README's ``curl`` line fetches the same URL.
    """
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    m = re.search(r"^site_url:\s*(\S+)\s*$", text, re.M)
    assert m, "mkdocs.yml declares no site_url"
    return m.group(1).rstrip("/") + "/install.sh"


def _fetch_url(source: str) -> str:
    """The URL just-runit fetches for a ``[tools.*]`` *source*.

    Only the two spellings jm's template uses are modelled: a direct
    ``https://`` URL, and ``just-bashit:NAME``. Any other form fails the
    test instead of passing it, so a new spelling has to be taught here,
    from just-runit's own ``_resolve``, before it can ship.
    """
    if source.startswith("https://"):
        return source
    namespace, sep, name = source.partition(":")
    if sep and namespace == "just-bashit":
        return f"{JBS_BASE}/{name}.sh"
    pytest.fail(f"no model of how just-runit resolves {source!r}; add one")


def _retrying():
    """nco_tone's ``_retrying`` (gh-1639): jm's one retry-on-a-blip helper.

    Loaded from the example rather than copied, so the two cannot disagree
    about what is transient. It retries a 5xx or a failure below HTTP, and
    re-raises anything else -- a 404 -- on the first attempt.
    """
    spec = importlib.util.spec_from_file_location("_nco_tone_1897", NCO_TONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._retrying


def test_the_just_makeit_source_is_jms_installer(bootstrap):
    """The one the README tells a user to run, wherever the site serves it."""
    source = bootstrap["tools"]["just-makeit"]["source"]
    assert source == _installer_url(), (
        f"[tools.just-makeit] source is {source!r}; jm's installer is "
        f"served at {_installer_url()}"
    )


@pytest.mark.network
def test_every_tool_source_fetches_a_script(bootstrap):
    retrying = _retrying()
    sources = {name: t["source"] for name, t in bootstrap["tools"].items()}
    assert {"install-deps", "just-makeit"} <= set(sources), sources
    failed = []
    for name, source in sorted(sources.items()):
        url = _fetch_url(source)

        def fetch(url: str = url) -> bytes:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read(2)

        try:
            head = retrying(fetch, f"fetching {url}")
        except urllib.error.HTTPError as exc:
            failed.append(f"[tools.{name}] {source!r}: {url} -> {exc.code}")
            continue
        if head != b"#!":
            failed.append(f"[tools.{name}] {source!r}: {url} is no script")
    assert not failed, (
        "`just-runit install` cannot fetch these, so it fails in every "
        "project jm scaffolds:\n  " + "\n  ".join(failed)
    )


def test_linux_dev_groups_name_patchelf(bootstrap):
    groups = bootstrap["dev"]
    needs = {
        manager
        for manager, body in install_functions(INSTALL_DEPS).items()
        if re.search(r"\bpatchelf\b", body)
    }
    checked = sorted(needs & set(groups))
    # Armed: apt is the manager CI's Linux legs use.
    assert "apt" in checked, (
        f"compared no apt group: install-deps installs patchelf for "
        f"{sorted(needs)}, the template has {sorted(groups)}"
    )
    missing = [m for m in checked if "patchelf" not in groups[m]["packages"]]
    assert not missing, (
        f"no patchelf in the template's {missing} dev group(s): auditwheel "
        "needs it to repair a Linux wheel, and jm's install-deps installs "
        "it for those managers"
    )
