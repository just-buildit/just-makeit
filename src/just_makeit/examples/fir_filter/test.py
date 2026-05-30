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
    pc_text = pc_files[0].read_text()
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
    config_text = config_files[0].read_text()
    assert "PACKAGE_PREFIX_DIR" in config_text, (
        "@PACKAGE_INIT@ not present in installed config file — "
        "find_package will fail after prefix change or DESTDIR staging"
    )

    # Step 4: build a minimal cmake consumer using find_package
    consumer = proj / "consumer_smoke"
    consumer.mkdir()
    (consumer / "smoke.c").write_text(
        '#include "my_fir.h"\nint main(void) { return 0; }\n'
    )
    (consumer / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(smoke C)\n"
        "find_package(my_fir REQUIRED)\n"
        "add_executable(smoke smoke.c)\n"
        "target_link_libraries(smoke PRIVATE my_fir::my_fir_lib_static)\n"
    )
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            *_cmake_gen(),
            f"-DCMAKE_PREFIX_PATH={install_prefix}",
        ],
        cwd=consumer,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=consumer)

    # Step 5: pkg-config smoke (Linux/macOS only — Windows has no pkg-config ABI)
    if sys.platform == "win32" or not shutil.which("pkg-config"):
        return
    pc_dir = next(
        (p for p in install_prefix.rglob("pkgconfig") if p.is_dir()), None
    )
    if pc_dir is None:
        return
    env = os.environ.copy()
    env["PKG_CONFIG_PATH"] = str(pc_dir)
    r = subprocess.run(
        ["pkg-config", "--exists", "my-fir"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"pkg-config --exists my-fir failed:\n{r.stderr}"
    r = subprocess.run(
        ["pkg-config", "--cflags", "--libs", "my-fir"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (
        f"pkg-config --cflags --libs my-fir failed:\n{r.stderr}"
    )
    assert "-lmy_fir" in r.stdout, (
        f"Expected -lmy_fir in pkg-config output; got: {r.stdout!r}"
    )


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._add import run as jm_add
    from just_makeit._perf import run as jm_perf

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

    # Verify jb.toml was generated with expected structure
    import tomllib

    with (proj / "jb.toml").open("rb") as f:
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
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 4. Add n_taps scalar state, rebuild, retest
    jm_add(proj, "fir_filter", [("n_taps", "int32_t", "16")])
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 5. Upgrade to perf annotations + scratch-buffer kernel
    jm_perf(proj)
    _cmd([sys.executable, str(STEPS / "07_patch.py")], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 6. Verify type stub
    pyi = (proj / "src" / "my_fir" / "fir_filter.pyi").read_text()
    assert "class FirFilter:" in pyi
    assert "def step(self, x: complex) -> complex:" in pyi
    assert "def steps(self, x: NDArray[np.complex64]" in pyi
    assert "n_taps" in pyi

    # 7. Install smoke: cmake --install + find_package consumer + pkg-config
    _install_smoke(proj)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("fir_filter: PASSED")
