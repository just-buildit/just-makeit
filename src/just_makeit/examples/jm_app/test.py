"""End-to-end test: jm app --target c|console|pep723 scaffolding.

Exercises all three app targets against a minimal EMA component, verifying
that each target produces the expected files with the correct content.  No
cmake build is performed — this is a pure scaffolding-correctness check.

  1. Scaffold an EMA project with two state vars (alpha, prev).
  2. Run `jm app --target c`       → native/src/app/ema_filter.c + CMakeLists.
  3. Run `--target c` a second time → idempotent (no duplicate cmake block).
  4. Run `jm app --target console` → src/my_ema/cli.py + pyproject.toml scripts.
  5. Run `jm app --target pep723`  → ema_filter.py at project root.
  6. Verify TOML config reflects the last call (pep723 wins).

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/jm_app/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


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
    from just_makeit._app import run as jm_app
    from just_makeit import _config as C

    # ── 1. Scaffold a minimal EMA project. ───────────────────────────────
    # Two ctor-visible state vars give argparse flags in all three targets.
    jm_new(
        "my_ema",
        root / "my_ema",
        object_names=["ema"],
        state_vars=[
            ("alpha", "float", "0.1f"),
            ("prev", "float", "0.0f"),
        ],
        arg_type="float",
        return_type="float",
    )
    proj = root / "my_ema"

    # ── 2. --target c: generates C executable stub + CMakeLists block. ───
    jm_app(proj, target="c", name="ema_filter", object_="ema")

    app_c = proj / "native" / "src" / "app" / "ema_filter.c"
    assert app_c.exists(), f"C app stub not created: {app_c}"

    app_c_text = app_c.read_text(encoding="utf-8")
    # The generated stub must include the component header so it can call
    # the create/destroy lifecycle functions.
    assert "ema/ema_core.h" in app_c_text, "C stub must include <ema/ema_core.h>"
    assert "ema_create" in app_c_text, "C stub must call ema_create"
    assert "ema_destroy" in app_c_text, "C stub must call ema_destroy"

    cmake_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_executable(ema_filter" in cmake_text, (
        "CMakeLists.txt must contain add_executable(ema_filter"
    )
    assert "ema_core" in cmake_text, "CMakeLists.txt must link against ema_core"

    # ── 3. Second --target c call must be idempotent. ────────────────────
    # _splice_cmake replaces the existing sentinel block rather than
    # appending a second one — verify only one add_executable line exists.
    jm_app(proj, target="c", name="ema_filter", object_="ema")

    cmake_text2 = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    count = cmake_text2.count("add_executable(ema_filter")
    assert count == 1, (
        f"CMakeLists.txt should have exactly one add_executable(ema_filter, "
        f"found {count}"
    )

    # ── 4. --target console: generates cli.py + pyproject.toml entry. ────
    jm_app(proj, target="console", name="ema_filter", object_="ema")

    cli_py = proj / "src" / "my_ema" / "cli.py"
    assert cli_py.exists(), f"Console CLI not created: {cli_py}"

    cli_text = cli_py.read_text(encoding="utf-8")
    # argparse must be imported and both state vars must appear as flags.
    assert "argparse" in cli_text, "cli.py must import argparse"
    assert "--alpha" in cli_text, "cli.py must have --alpha flag"
    assert "--prev" in cli_text, "cli.py must have --prev flag"
    # Constructor call must reference the class by its title-cased name.
    assert "Ema(" in cli_text, "cli.py must instantiate Ema(...)"

    pyproject_text = (proj / "pyproject.toml").read_text(encoding="utf-8")
    assert "ema_filter" in pyproject_text, (
        "pyproject.toml must contain the ema_filter script entry"
    )
    assert "my_ema.cli:main" in pyproject_text, (
        "pyproject.toml must point the script at my_ema.cli:main"
    )

    # ── 5. --target pep723: generates a self-contained inline script. ────
    jm_app(proj, target="pep723", name="ema_filter", object_="ema")

    pep723 = proj / "ema_filter.py"
    assert pep723.exists(), f"PEP 723 script not created: {pep723}"

    pep723_text = pep723.read_text(encoding="utf-8")
    # The PEP 723 script block identifies the file as a uv-runnable script
    # and pins the package dependency so recipients can run it standalone.
    assert "# /// script" in pep723_text, (
        "PEP 723 script must contain the # /// script block"
    )
    assert "my_ema" in pep723_text, "PEP 723 script must reference the my_ema package"

    # ── 6. TOML reflects the last run (pep723 wins). ─────────────────────
    # Each call to jm_app overwrites [app] in the TOML; the last one wins.
    cfg = C.load(proj)
    app_cfg = C.app_config(cfg)
    assert app_cfg["target"] == "pep723", (
        f"Expected last app target to be 'pep723', got {app_cfg['target']!r}"
    )
    assert app_cfg["object"] == "ema", (
        f"Expected app object to be 'ema', got {app_cfg['object']!r}"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("jm_app: PASSED")
