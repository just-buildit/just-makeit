"""The test environment carries the code-under-test's own dependencies.

``make test`` runs pytest under ``uv run --no-project``, which excludes the
project *and its dependencies*, while the suite imports ``just_makeit`` straight
from ``src/``. That combination means jm's runtime dependencies have to be
supplied to the test env explicitly — and when one is missing the failure does
not look like a missing dependency:

- ``tomlkit`` absent is **silent**. ``_config._write_doc`` falls back to
  ``_dump`` on purpose (it must stay importable where tomlkit isn't installed),
  so comment and key preservation quietly stop and the TOML round-trip tests
  fail on *content*. Eight failures across ``test_codec_*`` and ``test_app_gen``
  that all point at serialization rather than at the environment.
- ``tomli`` absent is **fatal** below 3.11, where it is ``C.tomllib``.

So the Makefile mirrors ``[project] dependencies``. Mirroring is duplication,
and duplication rots — this file is what stops it: pyproject stays the source of
truth, and the Makefile is checked against it.
"""

from __future__ import annotations

import re
from pathlib import Path

from just_makeit import _config as C

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"
MAKEFILE = ROOT / "Makefile"


def _declared_runtime_deps() -> set[str]:
    """Distribution names in ``[project] dependencies`` (the SSOT)."""
    data = C.tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = set()
    for spec in data["project"]["dependencies"]:
        # "tomli>=2.0.0; python_version < '3.11'" -> "tomli"
        names.add(re.split(r"[<>=!;\[ ]", spec.strip(), maxsplit=1)[0])
    return names


def _makefile_test_env_deps() -> set[str]:
    """Distribution names the Makefile feeds the isolated pytest env."""
    text = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(
        r"^JM_RUNTIME_DEPS\s*=(.*?)(?=\n[A-Z_]+\s*=|\n\n)", text, re.S | re.M
    )
    assert m, "JM_RUNTIME_DEPS not found in the Makefile"
    return {
        re.split(r"[<>=!;\[ ]", spec, maxsplit=1)[0]
        for spec in re.findall(r'--with\s+"([^"]+)"', m.group(1))
    }


class TestMakefileMirrorsPyproject:
    def test_no_runtime_dep_is_missing_from_the_test_env(self):
        declared = _declared_runtime_deps()
        supplied = _makefile_test_env_deps()
        missing = declared - supplied
        assert not missing, (
            f"{sorted(missing)} is declared in pyproject's [project] "
            "dependencies but not passed to the isolated pytest env in the "
            "Makefile's JM_RUNTIME_DEPS. `make test` would run the code under "
            "test without it — and a missing tomlkit fails silently."
        )

    def test_no_stale_dep_lingers_in_the_test_env(self):
        """The mirror goes both ways: a dropped dependency should not linger."""
        stale = _makefile_test_env_deps() - _declared_runtime_deps()
        assert not stale, (
            f"{sorted(stale)} is passed to the pytest env but is no longer a "
            "runtime dependency in pyproject — drop it from JM_RUNTIME_DEPS."
        )


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
