"""End-to-end test: running_stats scaffold → implement → build → add state
→ re-implement → build.

`jm add` is structural: adding state rewrites the state struct and rebuilds
the object from the manifest, resetting the hand-written step() body to a
fresh stub. The canonical loop is therefore to add the state, then
re-implement on top of it. This example demonstrates that loop and verifies
the rebuilt module computes the right statistics (mean, variance, min, max).

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/running_stats/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._add import run as jm_add

    # 1. Scaffold
    jm_new(
        "my_stats",
        root / "my_stats",
        object_names=["running_stats"],
        state_vars=[
            ("n", "int32_t", "0"),
            ("mean", "double", "0.0"),
            ("m2", "double", "0.0"),
        ],
    )
    proj = root / "my_stats"

    # Verify jb.toml was generated with expected structure
    import tomllib

    with (proj / "jb.toml").open("rb") as f:
        jbt = tomllib.load(f)
    assert jbt["project"]["name"] == "my_stats"
    assert jbt["tools"]["install-deps"]["source"] == "just-bashit:install-deps"
    assert "cmake" in jbt["dev"]["apt"]["packages"]

    # 2. Implement the base Welford step (mean + variance only)
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    # 3. CMake configure + build + CTest
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 4. Add min/max state variables. `jm add` is structural: it rewrites the
    #    state struct and rebuilds the object from the manifest, which resets
    #    the hand-written running_stats_step() body back to a fresh stub.
    #    This is the canonical loop — add state, then re-implement on top of it.
    jm_add(
        proj,
        "running_stats",
        [
            ("min_val", "double", "0.0"),
            ("max_val", "double", "0.0"),
        ],
        force=True,
    )

    # 5. Re-implement with the full algorithm that USES the new min/max state,
    #    then rebuild + retest. The default impl file (02_step_after.c) tracks
    #    min_val/max_val alongside the Welford mean/variance.
    _cmd(
        [sys.executable, str(STEPS / "02_patch.py"), "02_step_after.c"],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 6. Verify the rebuilt module computes the right statistics, including the
    #    newly added min/max state. This proves the re-implemented body really
    #    uses the state that `jm add` introduced.
    verify = (
        "import numpy as np\n"
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "from my_stats import RunningStats\n"
        "s = RunningStats()\n"
        "data = np.array([2, 4, 4, 4, 5, 5, 7, 9], dtype=np.complex64)\n"
        "for x in data:\n"
        "    y = s.step(x)\n"
        "assert abs(s.get_mean() - 5.0) < 1e-4, s.get_mean()\n"
        "assert abs(y.imag - 4.0) < 1e-4, y.imag\n"
        "assert abs(s.get_min_val() - 2.0) < 1e-9, s.get_min_val()\n"
        "assert abs(s.get_max_val() - 9.0) < 1e-9, s.get_max_val()\n"
        "s.reset()\n"
        "assert s.get_min_val() == 0.0 and s.get_max_val() == 0.0\n"
    )
    _cmd([sys.executable, "-c", verify], cwd=proj)

    # 5. Verify type stub reflects all state (including newly added vars)
    pyi = (proj / "src" / "my_stats" / "running_stats.pyi").read_text()
    assert "class RunningStats:" in pyi
    assert "min_val" in pyi
    assert "max_val" in pyi

    # 6. `jm bind` round-trip — proves the binding can be regenerated from
    # the header alone (no TOML consulted). This validates the Phase 1
    # bind MVP against a real, multi-field, mid-sized scaffolded project.
    from just_makeit._bind import run as jm_bind

    ext_c = proj / "native" / "src" / "running_stats" / "running_stats_ext.c"
    original = ext_c.read_text(encoding="utf-8")

    # Wipe the generated binding; the header + reset() defaults are all
    # `jm bind` has to work with.
    ext_c.unlink()
    jm_bind(proj, "running_stats")

    rebound = ext_c.read_text(encoding="utf-8")
    assert rebound == original, (
        "jm bind round-trip diverged from canonical scaffold:\n"
        f"diff len: original={len(original)} rebound={len(rebound)}"
    )

    # Rebuild + retest to prove the bound binding still compiles, links,
    # and passes the same CTest suite the original did.
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("running_stats: PASSED")
