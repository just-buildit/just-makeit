"""The test environment carries the code-under-test's own dependencies.

``make test`` runs pytest under ``uv run --no-project ... --with-editable .``:
the project env is kept out, and jm is installed into the isolated env, which
brings its ``[project] dependencies`` with it. When one of those is missing the
failure does not look like a missing dependency:

- ``tomlkit`` absent is **silent**. ``_config._write_doc`` falls back to
  ``_dump`` on purpose (it must stay importable where tomlkit isn't installed),
  so comment and key preservation quietly stop and the TOML round-trip tests
  fail on *content*. Eight failures across ``test_codec_*`` and ``test_app_gen``
  that all point at serialization rather than at the environment.
- ``tomli`` absent is **fatal** below 3.11, where it is ``C.tomllib``.

The Makefile used to mirror that list into the env by hand (``JM_RUNTIME_DEPS``)
and this file held the mirror to pyproject. Since gh-1374 installs jm itself,
pyproject is the only list, and the mirror went with gh-1551. What remains here
asserts the *outcome* in whatever env runs: the deps are really present.
"""

from __future__ import annotations

import re
from pathlib import Path

from just_makeit import _config as C

ROOT = Path(__file__).parent.parent


class TestDepsActuallyPresent:
    """Belt and braces: the deps are importable in whatever env is running.

    `conftest.py` already refuses to collect without them, so this passing is
    mostly a statement that the guard is wired up — but it fails loudly and
    specifically if someone removes the guard.
    """

    def test_tomlkit_importable(self):
        import tomlkit  # noqa: F401

    def test_write_doc_is_not_silently_degraded(self, tmp_path):
        """The real symptom, asserted directly: comments survive a round-trip.

        Without tomlkit `_write_doc` degrades to `_dump`, which rebuilds
        sections and strips the prose. That is exactly the gh-491 regression,
        and it is what the eight confusing failures were really reporting.
        """
        path = tmp_path / "just-makeit.toml"
        path.write_text(
            '[project]\nname = "demo"\n\n'
            "# why this component links what it links\n"
            '[acq]\narg_type = "float"\n',
            encoding="utf-8",
        )
        cfg = C.load(tmp_path)
        cfg["acq"]["return_type"] = "float"
        C.save(tmp_path, cfg)
        text = path.read_text(encoding="utf-8")
        assert "# why this component links what it links" in text, (
            "the manifest comment was stripped — _write_doc degraded to _dump, "
            "which means tomlkit is missing from this environment"
        )
        assert 'return_type = "float"' in text


class TestSystemDepsCoverWhatTheSuiteCompiles:
    """jm's own `bootstrap.toml` must provide what the projects it builds need.

    The suite scaffolds projects and compiles them, and a generated
    `CMakeLists.txt` does `find_package(Python … NumPy)` — so numpy's C headers
    have to be installed, not just importable. `bootstrap.toml` is the manifest for
    that, and `make install-deps` is what reads it.

    This drifted and CI did not notice for a while: jm SHIPS a `bootstrap.toml`
    template listing `python3-numpy` (and the per-platform equivalents) on
    every platform, while jm's OWN `bootstrap.toml` listed none. The matrix used to
    run `jm-install-deps`, which provisions numpy into a venv and so papered
    over it; when that step became `make install-deps` the provisioning went
    with it, and generated builds started failing on

        fatal error: numpy/arrayobject.h: No such file or directory

    intermittently, depending on which interpreter CMake happened to pick.

    Asserted against the shipped template rather than a hard-coded list, so it
    keeps holding as platforms are added: wherever the template provides numpy,
    jm's own manifest must too. The converse is deliberately not asserted --
    the template carries `gcc-c++` for user projects that jm itself has no use
    for, and forcing equality would be asserting a coincidence.
    """

    TEMPLATE = ROOT / "src/just_makeit/templates/toml/bootstrap.toml"
    OWN = ROOT / "bootstrap.toml"

    @staticmethod
    def _groups(path: Path) -> dict[str, list[str]]:
        """`{platform: [package, …]}` for each `[dev.<platform>]` table."""
        text = path.read_text(encoding="utf-8")
        out: dict[str, list[str]] = {}
        for m in re.finditer(
            r"^\[dev\.(\w+)\]\s*\npackages\s*=\s*\[(.*?)\]",
            text,
            re.M | re.S,
        ):
            out[m.group(1)] = re.findall(r'"([^"]+)"', m.group(2))
        return out

    def test_parses_both_manifests(self):
        """A regex that matches nothing would make the check below vacuous."""
        assert self._groups(self.TEMPLATE), "parsed no [dev.*] from template"
        assert self._groups(self.OWN), "parsed no [dev.*] from bootstrap.toml"

    def test_numpy_wherever_the_shipped_template_has_it(self):
        template, own = self._groups(self.TEMPLATE), self._groups(self.OWN)
        missing = []
        for platform, pkgs in template.items():
            if not any("numpy" in p for p in pkgs):
                continue
            if not any("numpy" in p for p in own.get(platform, [])):
                missing.append(platform)
        assert not missing, (
            f"bootstrap.toml's {missing} group(s) provide no numpy, but the template "
            "jm ships does. The suite compiles generated projects and their "
            "CMake does find_package(Python … NumPy), so `make install-deps` "
            "must install numpy's headers or the build fails on "
            "numpy/arrayobject.h."
        )
