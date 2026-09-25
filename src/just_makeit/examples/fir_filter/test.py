"""End-to-end test: fir_filter scaffold → implement → build → add state → perf → install.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/fir_filter/test.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from just_makeit._pyfmt import flatten_signatures

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd, env=None):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600, env=env
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def _install_smoke(proj: Path) -> None:
    """Install the built project and verify pkg-config + find_package both work."""
    install_prefix = proj / "install"

    # Step 1: install
    _cmd(
        ["cmake", "--install", "build", "--prefix", str(install_prefix)],
        cwd=proj,
    )

    # Step 2: verify pkg-config file content (no cmake binary needed)
    pc_files = list(install_prefix.rglob("*.pc"))
    assert pc_files, f"No .pc file installed under {install_prefix}"
    pc_text = pc_files[0].read_text(encoding="utf-8")
    assert "Cflags:" in pc_text
    assert "Libs:" in pc_text
    assert "CMAKE_INSTALL_FULL_" not in pc_text, (
        "pc file contains unexpanded CMake variable — absolute path baked in.\n"
        f"pc content:\n{pc_text}"
    )

    # Step 3: verify cmake config files exist and use @PACKAGE_INIT@
    config_files = list(install_prefix.rglob("*-config.cmake"))
    targets_files = list(install_prefix.rglob("*-targets.cmake"))
    assert config_files, f"No *-config.cmake installed under {install_prefix}"
    assert targets_files, (
        f"No *-targets.cmake installed under {install_prefix}"
    )
    config_text = config_files[0].read_text(encoding="utf-8")
    assert "PACKAGE_PREFIX_DIR" in config_text, (
        "@PACKAGE_INIT@ not present in installed config file — "
        "find_package will fail after prefix change or DESTDIR staging"
    )

    # Step 4: a find_package consumer of EACH flavour, which calls into the
    # library and is RUN. gh-1368: this used to be `main() { return 0; }`
    # against the static library, which proves the headers parse and nothing
    # else -- the Windows DLL exported no symbols at all and this stayed green
    # on every platform. `destroy(NULL)` is documented as safe, so the call
    # needs no constructor arguments and still crosses into the library.
    consumer = proj / "consumer_smoke"
    consumer.mkdir()
    (consumer / "smoke.c").write_text(
        '#include "my_fir.h"\n'
        "int main(void) { fir_filter_destroy(NULL); return 0; }\n",
        encoding="utf-8",
    )
    (consumer / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(smoke C)\n"
        "find_package(my_fir REQUIRED)\n"
        "add_executable(smoke_static smoke.c)\n"
        "target_link_libraries(smoke_static PRIVATE my_fir::my_fir-static)\n"
        "add_executable(smoke_shared smoke.c)\n"
        "target_link_libraries(smoke_shared PRIVATE my_fir::my_fir)\n",
        encoding="utf-8",
    )
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            f"-DCMAKE_PREFIX_PATH={install_prefix}",
        ],
        cwd=consumer,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=consumer)
    # The shared one has to FIND its library at run time: bin/ on Windows (the
    # DLL, installed under RUNTIME), lib*/ elsewhere.
    env = os.environ.copy()
    libdirs = [str(install_prefix / d) for d in ("bin", "lib", "lib64")]
    for var in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        env[var] = os.pathsep.join(libdirs + [env.get(var, "")])
    exe = ".exe" if os.name == "nt" else ""
    for flavour in ("smoke_static", "smoke_shared"):
        _cmd(
            [str(consumer / "build" / f"{flavour}{exe}")],
            cwd=consumer,
            env=env,
        )

    # Step 5: pkg-config smoke (Linux/macOS only — Windows has no pkg-config ABI)
    # POSIX only, as the heading says -- and said as a platform test, not as
    # "no pkg-config on PATH": a Windows runner HAS one, Strawberry Perl's
    # pkg-config.BAT, and it fails however it is started (gh-1368). The .pc
    # file's contents were already checked in step 2 without the binary.
    pkg_config = shutil.which("pkg-config")
    if os.name == "nt" or not pkg_config:
        return
    pc_dir = next(
        (p for p in install_prefix.rglob("pkgconfig") if p.is_dir()), None
    )
    if pc_dir is None:
        return
    env = os.environ.copy()
    env["PKG_CONFIG_PATH"] = str(pc_dir)
    r = subprocess.run(
        [pkg_config, "--exists", "my_fir"],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, f"pkg-config --exists my_fir failed:\n{r.stderr}"
    r = subprocess.run(
        [pkg_config, "--cflags", "--libs", "my_fir"],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, (
        f"pkg-config --cflags --libs my_fir failed:\n{r.stderr}"
    )
    assert "-lmy_fir" in r.stdout, (
        f"Expected -lmy_fir in pkg-config output; got: {r.stdout!r}"
    )


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._add import run as jm_add
    from just_makeit._perf import run as jm_perf
    from just_makeit._apply import run as apply_run

    # 1. Scaffold
    jm_new(
        "my_fir",
        root / "my_fir",
        object_names=["fir_filter"],
        state_vars=[
            ("coeffs", "float[16]", ""),
            ("delay", "float _Complex[16]", ""),
            ("gain", "float", "1.0"),
        ],
    )
    proj = root / "my_fir"

    # Verify bootstrap.toml was generated with expected structure
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib

    with (proj / "bootstrap.toml").open("rb") as f:
        jbt = tomllib.load(f)
    assert jbt["project"]["name"] == "my_fir"
    assert jbt["tools"]["install-deps"]["source"] == "just-bashit:install-deps"
    assert "cmake" in jbt["dev"]["apt"]["packages"]

    # 2. Implement the FIR step
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    # 3. CMake configure + build + CTest
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

    # 4. Add n_taps scalar state. `jm add` is structural: it rewrites the
    #    state struct and rebuilds the object from the manifest, which resets
    #    the hand-written step() body back to a fresh stub. Re-run the
    #    implement step so the FIR kernel is restored on top of the new state,
    #    then rebuild + retest.
    jm_add(proj, "fir_filter", [("n_taps", "int32_t", "16")], force=True)
    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 5. Upgrade to perf annotations + scratch-buffer kernel
    jm_perf(proj)
    _cmd([sys.executable, str(STEPS / "07_patch.py")], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 5b. Enrich the header with a real class summary, then regenerate the
    #     stub. The sacred header is the single source of truth for docs: the
    #     hand-written @brief on fir_filter_create() becomes the .pyi class
    #     docstring summary. `jm apply` re-derives the glue (.pyi included)
    #     from the edited header without touching the hand-patched kernel.
    _cmd([sys.executable, str(STEPS / "08_doxygen.py")], cwd=proj)
    apply_run(proj)

    # 6. Verify type stub
    # gh-744: signatures are wrapped to 79 cols when they do not fit,
    # so rejoin them before matching -- the assertion is about the
    # parameters, not where the line happens to break.
    pyi = flatten_signatures(
        (proj / "src" / "my_fir" / "fir_filter.pyi").read_text(
            encoding="utf-8"
        )
    )
    assert "class FirFilter:" in pyi
    assert "def step(self, x: complex) -> complex:" in pyi
    assert "def steps(self, x: NDArray[np.complex64]" in pyi
    assert "n_taps" in pyi

    # The header enrichment (step 5b) reached the class docstring: the real
    # @brief now leads the summary, not the generic "FirFilter component."
    assert "A 16-tap real-coefficient FIR filter" in pyi, (
        "enriched class summary missing from .pyi"
    )
    assert "FirFilter component." not in pyi, (
        "generic fallback summary still present — enrichment did not land"
    )

    # 7. Install smoke: cmake --install + find_package consumer + pkg-config
    _install_smoke(proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("fir_filter: PASSED")
