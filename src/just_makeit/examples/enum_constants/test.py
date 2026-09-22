"""End-to-end: bind a C enum whose values are not 0..n-1 (gh-1450).

A real C API rarely numbers its enum from zero without gaps: a sentinel sits
at -1, levels are spaced by ten, a first value starts at 1. An ``[[enum]]``
used to bind a choice to its POSITION, so every such API was bound wrong
without a diagnostic -- and -1 could not be passed at all, because the lookup
spent every negative value on "not found".

``enumerators`` names each choice's C constant, and every path a choice
crosses then carries the constant rather than the index:

  1. Scaffold a ``gate`` object.
  2. Declare the ``[[enum]]`` with its ``enumerators``, and use it three
     ways: a constructor parameter, a writable property, a method parameter.
  3. Assert the binding spells the constants, and never an index.
  4. Write the C enum (-1, 10, 20, 30) and the kernels, re-apply so the
     stub picks up each constant's ``///<`` doc, and ``status --check``.
  5. Build, CTest, and drive every path from Python: -1 in both
     directions, and an unknown string refused naming the choices.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 src/just_makeit/examples/enum_constants/test.py
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

#: Step 2: the enum, declared once at the top of the manifest.
ENUM_TOML = """
[[enum]]
name = "level"
values = ["auto", "debug", "info", "warn"]
enumerators = ["LVL_AUTO", "LVL_DEBUG", "LVL_INFO", "LVL_WARN"]
"""

#: Step 2: the three places the object uses it.
GATE_TOML = """
[[gate.init_params]]
name = "level"
type = "enum:level"
default = "auto"

[[gate.properties]]
name = "threshold"
type = "int"
field = true
writable = true
enum = "level"

[[gate.methods]]
name = "passes"
return_type = "int"
params = [{ name = "level", type = "int", enum = "level" }]
"""

#: Step 4: the author's C enum -- the only place its values are written.
C_ENUM = """/** @brief Log levels, spaced as in Python's `logging`. */
typedef enum {
    LVL_AUTO = -1, ///< Let the gate choose (it picks `info`).
    LVL_DEBUG = 10, ///< Diagnostic detail.
    LVL_INFO = 20, ///< Normal operation.
    LVL_WARN = 30, ///< Something needs attention.
} gate_level_t;

"""

#: Step 5: every path, from Python.
DEMO = """
import sys
sys.path.insert(0, "src")
from levels import Gate

g = Gate()  # "auto" -> LVL_AUTO (-1); the C resolves it to info
assert g.threshold == "info", g.threshold

g = Gate(level="debug")  # LVL_DEBUG, 10 -- not index 1
assert g.threshold == "debug"
assert g.passes("warn") == 1 and g.passes("debug") == 1

g.threshold = "warn"  # the setter stores LVL_WARN, 30
assert g.passes("info") == 0 and g.passes("warn") == 1

g.threshold = "auto"  # -1 is a legal value, in both directions
assert g.threshold == "auto"

try:
    g.passes("verbose")
except ValueError as e:
    assert "choices: auto, debug, info, warn" in str(e), e
else:
    raise AssertionError("an unknown level must be refused")
print("every choice reached C as its constant")
"""


def _jm(proj: Path, *args: str) -> str:
    """``just-makeit <args>`` in *proj*, in this process; its output."""
    from just_makeit._cli import main

    buf = io.StringIO()
    argv, cwd = sys.argv, os.getcwd()
    sys.argv = ["just-makeit", *args]
    os.chdir(proj)
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = argv
        os.chdir(cwd)
    assert code == 0, f"jm {' '.join(args)} exited {code}\n{buf.getvalue()}"
    return buf.getvalue()


def _cmd(args, cwd) -> str:
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
    assert r.returncode == 0, (
        f"{' '.join(map(str, args))} exited {r.returncode}\n"
        f"{r.stdout}\n{r.stderr}"
    )
    return r.stdout


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path}: anchor moved: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def run(root: Path) -> None:
    # ── 1. Scaffold ──────────────────────────────────────────────────────
    # The default is LVL_INFO's value. A C constant would do for the C,
    # but the scaffolded Python test spells the default too, and there a C
    # name means nothing.
    _jm(
        root,
        "new",
        "levels",
        "--object",
        "gate",
        "--no-step",
        "--state",
        "threshold:int:20",
    )
    proj = root / "levels"

    # ── 2. Declare the enum, and use it three ways ───────────────────────
    with open(proj / "just-makeit.toml", "a", encoding="utf-8") as f:
        f.write(ENUM_TOML)
    with open(proj / "objects" / "gate.toml", "a", encoding="utf-8") as f:
        f.write(GATE_TOML)
    # The Python test was scaffolded for the constructor `jm new` wrote; this
    # one takes `level`. Never edited, so it takes today's render.
    (proj / "src/levels/tests/test_gate.py").unlink()
    _jm(proj, "apply")

    # ── 3. The binding spells constants, never an index ──────────────────
    ext = (proj / "native/src/gate/gate_ext.c").read_text(encoding="utf-8")
    assert 'if (strcmp(level_str, "auto") == 0) level = LVL_AUTO;' in ext
    assert "static const int _enum_Gate_level_c[] = {" in ext
    for i in range(4):
        assert f") level = {i};" not in ext, "a choice bound to its index"

    # ── 4. The author's C, then re-apply for the docs ────────────────────
    hdr = proj / "native/inc/gate/gate_core.h"
    _replace_once(
        hdr,
        "/**\n * @brief Gate state.",
        C_ENUM + "/**\n * @brief Gate state.",
    )
    core = proj / "native/src/gate/gate_core.c"
    _replace_once(
        core, "gate_create(int threshold)\n{", "gate_create(int level)\n{"
    )
    _replace_once(
        core,
        "    obj->threshold = threshold;",
        "    obj->threshold = level == LVL_AUTO ? LVL_INFO : level;",
    )
    _replace_once(
        core,
        "    (void)state; (void)level;\n    return (int)0;",
        "    return level >= state->threshold;",
    )
    _jm(proj, "apply")
    pyi = (proj / "src/levels/gate.pyi").read_text(encoding="utf-8")
    assert '- ``"auto"`` — Let the gate choose' in pyi, pyi
    _jm(proj, "status", "--check")

    # ── 5. Build, test, and cross every path ─────────────────────────────
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], proj)
    out = _cmd([sys.executable, "-c", DEMO], proj)
    assert "every choice reached C as its constant" in out, out


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("enum_constants: PASSED")
