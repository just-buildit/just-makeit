"""The nco_tone doppler pin, and the advisory that reports when it lags.

`_DOPPLER_VERSION` is what a local run downloads; `nco_tone_ci.yml` downloads
doppler's *latest* release instead, deliberately. The two paths agree only
while the pin tracks the latest release.

**Why the currency report is advisory rather than a gate**, pinned here so the
reasoning is not lost the next time someone wants to give it teeth: the pin
drifts because a different repository published, not because of anything in
the change being linted. Measured 2026-08-30 — doppler shipped five releases
in thirty days — so a hard gate would redden this repo roughly weekly and block
PRs with nothing to do with doppler. That is the `standard-check` shape, whose
cost this repo already knows.

What actually protects the example is `test_example[nco_tone]` building
against the pin. The report is a sighting, not a guard, and the tests below
assert exactly that: it must never return non-zero, in any of its three states.

**That hole is closed.** A local doppler install used to SHADOW the pin —
`_find_doppler_prefix` scanned `/usr/local`, `/usr`, `~/.local/doppler`,
`~/.local` and `~/doppler/build` before downloading — so on a developer box the
pin was never exercised, which is how a stale one shipped unnoticed. Measured
2026-09-16: a two-week-old `~/doppler/build` shadowed it at 0.46.0 while CI ran
0.49.0 and the pin said 0.49.0, and the run reported it as "version unknown".

The example now resolves doppler's LATEST release and downloads it, matching
what CI does, and a machine-local prefix is opt-IN via `--doppler-prefix`. The
pin is the offline fallback rather than the target — which is what keeps this
currency report worth running: the fallback must not rot.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import check_doppler_pin as C  # noqa: E402

PIN_FILE = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "nco_tone"
    / "test.py"
)


def test_it_reads_the_real_pin():
    """Against the real file, so a rename or a reformat is caught here rather
    than by the check quietly reporting nothing forever."""
    pin = C.read_pin()
    assert pin is not None, "_DOPPLER_VERSION not found in the example"
    assert pin[0].isdigit(), pin
    assert f'_DOPPLER_VERSION = "{pin}"' in PIN_FILE.read_text(
        encoding="utf-8"
    )


def test_the_pin_is_at_or_above_the_floor_the_example_needs():
    """doppler v0.39.0 added the trailing capacity argument to
    `nco_steps_u32` that the example's step() body passes. Below that the
    example cannot compile, whatever the currency report says — this is the
    invariant with teeth, and it needs no network."""
    assert C._key(C.read_pin()) >= C._key("0.39.0")


def test_behind_is_reported_but_does_not_fail(capsys, monkeypatch):
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.30.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0, "the currency report must stay advisory"
    out = capsys.readouterr().out
    assert "BEHIND" in out
    assert "0.30.0" in out and "0.45.0" in out
    assert "advisory" in out
    assert "_DOPPLER_VERSION" in out, "must name what to change"


def test_current_is_reported(capsys, monkeypatch):
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.45.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0
    assert "current" in capsys.readouterr().out


def test_ahead_of_latest_is_not_reported_as_behind(capsys, monkeypatch):
    """A pin bumped to an unpublished tag, or a yanked release, must not read
    as drift — `>=`, not `==`."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.46.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0
    assert "BEHIND" not in capsys.readouterr().out


def test_unreachable_network_says_so_rather_than_passing_silently(
    capsys, monkeypatch
):
    """Offline is not evidence the pin is current. It must not fail — being
    offline is not the PR's fault — but it must not print a clean-looking
    nothing either, which is the shape that makes a check useless."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.45.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: None)
    assert C.main() == 0
    out = capsys.readouterr().out
    assert "unknown" in out and "0.45.0" in out


def test_a_missing_pin_constant_is_announced(capsys, monkeypatch):
    """If the constant is renamed the check loses its subject. Saying so beats
    reporting a confident nothing."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: None)
    assert C.main() == 0
    assert "no _DOPPLER_VERSION" in capsys.readouterr().out


def test_version_keys_compare_numerically():
    assert C._key("0.9.0") < C._key("0.10.0"), "string compare would invert"
    assert C._key("0.45.0") == C._key("0.45.0")
    assert C._key("1.0.0") > C._key("0.99.9")


# ── the floor only has teeth where the version is READABLE ──────────────────
#
# gh: measured 2026-09-16. `_find_doppler_prefix` lists `~/doppler/build` as an
# explicit candidate, and a source BUILD TREE writes `doppler.pc` at the top of
# the build dir rather than under `lib/pkgconfig/`. `_prefix_version` looked
# only under `lib/` and `lib64/`, so every build-tree prefix reported "version
# unknown" -- and an unknown version SKIPS the floor check, because the caller
# accepts a prefix it merely could not measure.
#
# So the floor, which the pin comment calls "the part with teeth", had none for
# the commonest local-dev prefix. That is exactly the `too many arguments to
# nco_steps_u32` failure it was added to stop, which had already happened twice.


def _example_module():
    """The nco_tone example's test module, loaded by path.

    It is not importable as a package (it is example payload, not library
    code), so it is loaded the way the example runner loads it.
    """
    import importlib.util as u

    spec = u.spec_from_file_location("_nco_tone_example", PIN_FILE)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prefix_with_pc(root: Path, rel: str, version: str) -> Path:
    pc = root / rel / "doppler.pc"
    pc.parent.mkdir(parents=True, exist_ok=True)
    pc.write_text(f"Name: doppler\nVersion: {version}\n", encoding="utf-8")
    return root


def test_a_release_layout_version_is_read(tmp_path):
    m = _example_module()
    p = _prefix_with_pc(tmp_path / "rel", "lib/pkgconfig", "0.49.0")
    assert m._prefix_version(p) == "0.49.0"


def test_a_build_tree_version_is_read(tmp_path):
    """The regression: `.pc` at the prefix ROOT, as a cmake build dir writes
    it. This returned None, which reads as 'cannot judge' and waves the
    prefix through regardless of the floor."""
    m = _example_module()
    p = _prefix_with_pc(tmp_path / "bld", ".", "0.46.0")
    assert m._prefix_version(p) == "0.46.0"


def test_a_prefix_with_no_pc_is_still_unjudgeable(tmp_path):
    """The None path must survive -- it is what lets a prefix jm genuinely
    cannot measure be accepted rather than rejected."""
    m = _example_module()
    (tmp_path / "bare").mkdir()
    assert m._prefix_version(tmp_path / "bare") is None


def test_a_build_tree_below_the_floor_is_now_measurable(tmp_path):
    """The consequence that matters: below-floor + build-tree layout used to
    be indistinguishable from unmeasurable, so it was accepted and failed
    later at COMPILE with an error that reads as a bug in the example."""
    m = _example_module()
    p = _prefix_with_pc(tmp_path / "old", ".", "0.38.0")
    found = m._prefix_version(p)
    assert found == "0.38.0"
    assert m._version_key(found) < m._version_key(m._DOPPLER_FLOOR)


# ── the scanning must not come back ─────────────────────────────────────────


def test_it_does_not_scan_the_machine_for_doppler():
    """The whole point of gh-1310's follow-up: this example is for jm/doppler
    USERS, so what is installed on the box must not decide what it builds
    against. Re-adding any of these candidates silently restores a shadow that
    made three paths disagree, and the symptom is invisible — everything still
    passes, against the wrong version.
    """
    src = PIN_FILE.read_text(encoding="utf-8")
    body = src[src.index("def _find_doppler_prefix") :]
    body = body[: body.index("\n# ──")] if "\n# ──" in body else body
    for candidate in (
        'Path("/usr/local")',
        'Path("/usr")',
        '"doppler" / "build"',
        '.local" / "doppler"',
    ):
        assert candidate not in body, (
            f"_find_doppler_prefix scans {candidate} again; a machine-local "
            f"prefix is opt-in via --doppler-prefix"
        )


def test_the_explicit_prefix_escape_hatch_is_still_documented():
    """Removing the scan is only acceptable because there is an explicit way
    to name a prefix; if that goes, a doppler developer has none."""
    src = PIN_FILE.read_text(encoding="utf-8")
    assert "--doppler-prefix" in src


def test_the_pin_is_the_fallback_not_the_target():
    """`latest_release()` must be consulted first. If the pin becomes the
    primary again, local and CI silently diverge the next time doppler ships.

    Read from the AST with the DOCSTRING DROPPED. A text scan matched the
    docstring's own prose about `_DOPPLER_VERSION` — the function explains
    that the pin is the fallback, and saying so put the name earlier in the
    file than the code that uses it. A detector looking for a name that the
    surrounding prose also discusses has to read the code, not the file.
    """
    import ast

    tree = ast.parse(PIN_FILE.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_find_doppler_prefix"
    )
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    calls_latest = [
        n.lineno
        for stmt in body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "latest_release"
    ]
    reads_pin = [
        n.lineno
        for stmt in body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Name) and n.id == "_DOPPLER_VERSION"
    ]
    assert calls_latest, "_find_doppler_prefix never calls latest_release()"
    assert reads_pin, "the pin is not used as a fallback at all"
    assert min(calls_latest) < min(reads_pin), (
        "the pin is consulted before the release lookup"
    )


def test_the_resolver_has_one_implementation():
    """`scripts/check_doppler_pin.py` must delegate rather than carry a second
    GitHub-API resolver; two copies of one primitive drift."""
    script = (
        Path(__file__).parent.parent / "scripts" / "check_doppler_pin.py"
    ).read_text(encoding="utf-8")
    assert "_example().latest_release(" in script
    assert "urlopen" not in script, "the script grew its own resolver again"
