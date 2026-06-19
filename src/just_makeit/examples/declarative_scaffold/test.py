"""End-to-end test: the declarative scaffolding workflow.

Demonstrates the schema-6 upgrade added in this release:

  1. Author a complete component spec in ONE TOML fragment, with the
     `step()` body inlined and `{Component}` placeholder interpolation.
  2. `jm apply <fragment>` materializes the project from the manifest
     plus the fragment alone — no per-CLI-call scaffolding script.
  3. cmake + ctest pass.
  4. `jm split-objects` round-trips a legacy single-file project into
     the split layout and the merged cfg is byte-identical.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/declarative_scaffold/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


# A whole DSP component declared in one TOML fragment. The step() body
# is inlined; `{Component}` is interpolated when `jm apply` runs.
_AGC_FRAGMENT = '''\
# objects/agc.toml — the whole AGC component in one fragment.
[agc]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "true"
no_state = "false"
no_step = "false"

# Inline step() body. `{Component}` is filled in by `jm apply` (here
# it becomes `Agc`). Literal C braces and unknown placeholders pass
# through untouched, so most algorithm bodies need no escaping.
impl = """
/* {Component} — EMA power tracker + gain pass-through. */
const float mag2 = crealf(x) * crealf(x) + cimagf(x) * cimagf(x);
state->power = state->power + state->alpha * (mag2 - state->power);
return (float _Complex)(state->gain * x);
"""

[[agc.state]]
name = "alpha"
type = "float"
default = "0.05f"

[[agc.state]]
name = "power"
type = "float"
default = "0.0f"

[[agc.state]]
name = "gain"
type = "float"
default = "1.0f"
'''


def run(root: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new
    from just_makeit._split_objects import run as jm_split_objects

    # ── 1. Empty project — manifest only, no objects yet. ────────────────
    proj = root / "demo"
    jm_new("demo", proj)

    # ── 2. Author the entire component spec in one fragment. ─────────────
    fragment = root / "agc.toml"
    # Explicit utf-8 — _AGC_FRAGMENT has non-ASCII (em-dashes) and on
    # Windows the default `write_text` encoding is cp1252; tomllib reads
    # strictly as UTF-8 and would reject any other encoding.
    fragment.write_text(_AGC_FRAGMENT, encoding="utf-8")

    # ── 3. `jm apply` materializes the full project from the fragment. ───
    jm_apply(proj, fragment=fragment)

    # Manifest now uses the split layout.
    manifest = (proj / C.FILENAME).read_text(encoding="utf-8")
    assert 'include = ["objects/*.toml"]' in manifest
    assert "[agc]" not in manifest, (
        "object spec must live in its fragment, not the manifest"
    )
    assert (proj / "objects" / "agc.toml").exists()

    # The {Component} placeholder was interpolated to `Agc`.
    core_h = (proj / "native" / "inc" / "agc" / "agc_core.h").read_text(
        encoding="utf-8"
    )
    assert "/* Agc — EMA power tracker" in core_h, core_h
    assert "{Component}" not in core_h

    # ── 4. cmake + build + ctest. ────────────────────────────────────────
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # The C extension itself must have been compiled — if `apply` hadn't
    # wired `add_subdirectory(native/src/agc)` into the top CMakeLists,
    # `cmake --build` would silently succeed without building it and
    # `ctest` would happily pass with zero registered tests. Assert.
    so_files = list((proj / "src" / "demo").glob("agc*"))
    so_files = [p for p in so_files if p.suffix == ".so"]
    assert so_files, (
        f"agc extension was not compiled into src/demo/ — "
        f"apply did not reconcile the top CMakeLists. "
        f"build dir contents: "
        f"{list((proj / 'build').glob('**/*agc*'))}"
    )

    # ── 5. split-objects round-trip on a separate legacy project. ────────
    # An existing single-file project converts to the split layout in
    # one command; the merged cfg every consumer sees is unchanged.
    legacy = root / "legacy"
    jm_new(
        "legacy",
        legacy,
        object_names=["widget"],
        state_vars=[("g", "float", "1.0f")],
    )
    before = C.load(legacy)
    jm_split_objects(legacy)
    after = C.load(legacy)
    assert before == after, "split-objects must not change the merged cfg"
    assert (legacy / "objects" / "widget.toml").exists()
    legacy_manifest = (legacy / C.FILENAME).read_text(encoding="utf-8")
    assert "[widget]" not in legacy_manifest
    assert 'include = ["objects/*.toml"]' in legacy_manifest


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("declarative_scaffold: PASSED")
