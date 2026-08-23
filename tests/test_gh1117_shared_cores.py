"""gh-1117: `jm status --shared-cores` reports cores linked into >1 module.

CPython imports extensions RTLD_LOCAL and jm links a component's OBJECT
library statically into every `.so` that needs it, so a core linked into three
modules is three copies of every file-scope `static` in it. For a pure kernel
that is correct. For a primitive whose contract is one-per-process it is
silently wrong, and doppler#976 is what that costs: an interrupt flag set
through one module left the waits in two others spinning on a different
variable, with every test passing because the only setter and the only
exercised wait happened to share a `.so`.

Two design choices here were **measured, not reasoned**, and the tests pin
both because both are the kind of thing a later change quietly reverses:

- **Opt-in.** Run over doppler's real manifest the detector lists 45 cores
    across 33 modules. Printed by default that is permanent noise on a correct
    project.
- **Uncounted.** For the same reason it must not move `jm status --check`'s
    exit code: a shared pure kernel is what the OBJECT-library wiring is *for*,
    and failing CI on it would make the report something to delete.

jm reports the linkage it owns — it wrote the `target_link_libraries` line —
and deliberately does not read the component's C to decide whether the core
really holds process-global state. That would be a model of C living in jm.

Driven through the CLI on gh-975's rule: the defect is about what a user sees
from `jm status`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Three standalone objects; two of them link the third's core.

    Standalone objects each get their own `.so`, so this is the smallest real
    instance of the shape: `shared_core`'s symbols land in three separate
    extension modules.
    """
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for name in ("shared", "alpha", "beta"):
        assert (
            _cli(
                "object", name, "--state", "g:double:1.0", cwd=root
            ).returncode
            == 0
        )
    # `depends_on` is TOML-only (repeatable, multi-attribute), so it is
    # declared by editing the manifest rather than through a flag.
    #
    # A scaffold uses the SPLIT layout, so `[alpha]` lives in
    # `objects/alpha.toml` and not in the central file. Writing to
    # `just-makeit.toml` here silently added nothing and the fixture built a
    # project with no shared core at all -- caught only because the assertions
    # then failed. The fixture asserts its own edit landed for that reason.
    for name in ("alpha", "beta"):
        frag = root / "objects" / f"{name}.toml"
        text = frag.read_text(encoding="utf-8")
        assert f"[{name}]\n" in text, text
        frag.write_text(
            text.replace(
                f"[{name}]\n",
                f'[{name}]\ndepends_on = [{{ name = "shared", link = true }}]\n',
                1,
            ),
            encoding="utf-8",
        )
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


def test_flag_names_the_core_and_every_module_linking_it(project):
    r = _cli("status", "--shared-cores", cwd=project)
    assert "SHARED CORES" in r.stdout, r.stdout
    line = next(
        ln for ln in r.stdout.splitlines() if "shared_core" in ln and "◆" in ln
    )
    for mod in ("shared", "alpha", "beta"):
        assert mod in line, line


def test_it_is_opt_in(project):
    """45 rows on a real project — default-on would be permanent noise."""
    assert "SHARED CORES" not in _cli("status", cwd=project).stdout


def test_it_does_not_change_the_exit_code(project):
    """A shared pure kernel is correct. Counting it would fail green CI.

    Note where this property is really gated: folding `len(_shared)` into
    `run`'s return trips the FIXTURE's own baseline assertion first, so the
    regression surfaces as an error on every test using it rather than as a
    failure here. That was measured — an earlier sabotage of the wrong
    `return drift_count` (there are two; the first serves `--json`) passed
    this test cleanly, which is what a vacuous check looks like.
    """
    assert _cli("status", "--check", cwd=project).returncode == 0
    assert (
        _cli("status", "--check", "--shared-cores", cwd=project).returncode
        == 0
    )


def test_every_module_kind_counts_as_a_module():
    """A detector blind to one kind under-reports exactly the shared case.

    Unit-level on purpose: scaffolding one project per kind through the CLI
    would take minutes to assert a mapping the manifest already determines.
    """
    from just_makeit import _procglobal

    cfg = {
        "k": {},  # the shared core
        "solo": {"depends_on": [{"name": "k", "link": True}]},
        "grouped": {"depends_on": [{"name": "k", "link": True}]},
        "module": {
            # object module: owns `grouped`, so `grouped` is NOT its own module
            "grp": {"objects": ["grouped"]},
            # kind-bearing: linkage rides on depends_on
            "sink": {
                "kind": "handle",
                "depends_on": [{"name": "k", "link": True}],
            },
        },
    }
    cores = _procglobal.module_cores(cfg)
    assert "grouped" not in cores, "an owned object is not its own module"
    assert set(cores) == {"k", "solo", "grp", "sink"}
    shared = {s.core: set(s.modules) for s in _procglobal.shared_cores(cfg)}
    assert shared["k_core"] == {"k", "solo", "grp", "sink"}


def test_a_dependency_without_link_is_not_linkage():
    """`depends_on` without `link` gives headers and aggregate-library
    objects, not symbols in this `.so`. Counting it would report a share
    that does not exist."""
    from just_makeit import _procglobal

    cfg = {"k": {}, "a": {"depends_on": ["k"]}, "b": {"depends_on": ["k"]}}
    assert _procglobal.shared_cores(cfg) == []


def test_json_always_carries_it(project):
    """A machine reader can filter to the core it cares about; a person
    scanning 45 lines cannot, which is why only the human report is opt-in."""
    r = _cli("status", "--json", cwd=project)
    report = json.loads(r.stdout)
    entry = next(
        e for e in report["shared_cores"] if e["core"] == "shared_core"
    )
    assert sorted(entry["modules"]) == ["alpha", "beta", "shared"]


def test_an_unshared_project_reports_nothing(tmp_path: Path):
    """The common case costs one manifest pass and says nothing at all."""
    assert _cli("new", "solo", cwd=tmp_path).returncode == 0
    root = tmp_path / "solo"
    assert (
        _cli(
            "object", "engine", "--state", "g:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    r = _cli("status", "--shared-cores", cwd=root)
    assert "SHARED CORES" not in r.stdout
    assert (
        json.loads(_cli("status", "--json", cwd=root).stdout)["shared_cores"]
        == []
    )
