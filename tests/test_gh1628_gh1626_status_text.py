"""`jm status` advice must be decided by what it describes (gh-1628, gh-1626).

Two sentences in the drift report were printed by a condition other than the
one they talk about:

- **gh-1628.** The footer "Your `_core.c` is yours -- apply only ADDS ..."
  ended EVERY drift report, so a tree whose only drift was a regenerated
  glue file closed with a sentence about a file the report did not name. It
  is now printed by the same `_sacred` list the "STALE -- yours" section
  prints.
- **gh-1626.** UNWIRED told every author "`jm apply` writes the missing
  target_sources() line". For a `no_generate` module (or a c_dep) apply
  writes only an `add_subdirectory`, so the promise could never come true
  and `--check` stayed red however often it was re-run. The advice is now
  per core and read from the replay `status` already runs -- the cores
  `apply` left unwired in its scratch copy, and the ones its render wires.

Every test drives real scaffolded trees through the CLI, and the advice is
checked against what `apply` then DOES, not only against its wording.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli

FOOTER = "Your `_core.c` is yours"


def _cli(*args, cwd) -> JmRun:
    return run_cli(*args, cwd=cwd)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """`jm new p --object g`, reconciled, so the baseline is clean."""
    assert _cli("new", "p", "--object", "g", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    assert _cli("apply", cwd=root).returncode == 0
    base = _cli("status", "--check", cwd=root)
    assert base.returncode == 0, base.stdout
    return root


# ── gh-1628: the footer ─────────────────────────────────────────────────────


def _drift_glue(root: Path) -> None:
    """The issue's trigger: a regenerated glue file, no sacred one."""
    p = root / "native/inc/p/p.h"
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")


def _drift_sacred(root: Path) -> None:
    """Remove a definition `_core.c` must have, so the sacred file is STALE
    (`apply` will ADD it back) -- gh-1337's shape."""
    c = root / "native/src/g/g_core.c"
    t = c.read_text(encoding="utf-8")
    i = t.index("g_reset(")
    start = t.rindex("\n", 0, t.rindex("\n", 0, i)) + 1
    end = t.index("\n}\n", i) + 3
    c.write_text(t[:start] + t[end:], encoding="utf-8")


@pytest.mark.parametrize("flags", [(), ("--check",)])
def test_glue_only_drift_says_nothing_about_core_c(project: Path, flags):
    _drift_glue(project)
    r = _cli("status", *flags, cwd=project)
    assert r.returncode == 1, r.stdout
    assert "native/inc/p/p.h" in r.stdout, r.stdout
    assert "_core.c" not in r.stdout, r.stdout


@pytest.mark.parametrize("flags", [(), ("--check",)])
def test_a_stale_sacred_file_keeps_the_footer(project: Path, flags):
    _drift_sacred(project)
    r = _cli("status", *flags, cwd=project)
    assert r.returncode == 1, r.stdout
    assert "native/src/g/g_core.c" in r.stdout, r.stdout
    assert "yours; `jm apply` will ADD" in r.stdout, r.stdout
    assert FOOTER in r.stdout, r.stdout


def test_other_drift_without_a_sacred_file_has_no_footer(project: Path):
    """Not only STALE glue: an UNWIRED-only report printed it too."""
    _no_generate_module(project)
    r = _cli("status", cwd=project)
    assert "UNWIRED (1)" in r.stdout, r.stdout
    assert FOOTER not in r.stdout, r.stdout


# ── gh-1626: the UNWIRED advice ─────────────────────────────────────────────


def _no_generate_module(root: Path) -> None:
    """The issue's trigger: a hand-written module whose OBJECT core apply
    never wires, reconciled twice as the reporter did."""
    with (root / "just-makeit.toml").open("a", encoding="utf-8") as f:
        f.write(
            "\n[module.timing]\nno_generate = true\n"
            'no_generate_reason = "hand-written timing core"\n'
            "objects = []\n"
        )
    d = root / "native/src/timing"
    d.mkdir(parents=True)
    (d / "t.c").write_text("int t(void){return 1;}\n", encoding="utf-8")
    (d / "CMakeLists.txt").write_text(
        "add_library(timing_obj OBJECT t.c)\n"
        "set_target_properties(timing_obj PROPERTIES"
        " POSITION_INDEPENDENT_CODE ON)\n",
        encoding="utf-8",
    )
    assert _cli("apply", cwd=root).returncode == 0
    assert _cli("apply", cwd=root).returncode == 0


def _unwire(root: Path, core: str) -> None:
    p = root / "CMakeLists.txt"
    body = p.read_text(encoding="utf-8")
    out = re.sub(
        rf"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:{core}>\)\n",
        "",
        body,
        flags=re.M,
    )
    assert out != body
    p.write_text(out, encoding="utf-8")


def test_no_generate_core_is_not_promised_an_apply(project: Path):
    _no_generate_module(project)
    r = _cli("status", cwd=project)
    assert "timing_obj (native/src/timing)" in r.stdout, r.stdout
    assert "`jm apply` writes the missing" not in r.stdout, r.stdout
    assert "`jm apply` writes no line for timing_obj" in r.stdout, r.stdout


def test_the_line_it_prints_is_one_apply_keeps_and_status_accepts(
    project: Path,
):
    """Not misdirecting: do exactly what the advice says, then re-run
    `apply` -- the line must survive and the finding must clear."""
    _no_generate_module(project)
    r = _cli("status", cwd=project)
    lines = re.findall(r"^    (target_sources\(.*\))$", r.stdout, re.M)
    assert lines == [
        "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:timing_obj>)",
        "target_sources(p_lib_static PRIVATE $<TARGET_OBJECTS:timing_obj>)",
    ], r.stdout
    cm = project / "CMakeLists.txt"
    cm.write_text(
        cm.read_text(encoding="utf-8") + "".join(f"{ln}\n" for ln in lines),
        encoding="utf-8",
    )
    assert _cli("apply", cwd=project).returncode == 0
    for ln in lines:
        assert ln in cm.read_text(encoding="utf-8"), "apply dropped it"
    after = _cli("status", "--check", cwd=project)
    assert after.returncode == 0, after.stdout
    assert "UNWIRED" not in after.stdout, after.stdout


def test_a_generated_core_still_says_apply_and_apply_does_it(project: Path):
    """The other half: the promise stays where it is true."""
    _unwire(project, "g_core")
    r = _cli("status", cwd=project)
    assert "g_core (native/src/g)" in r.stdout, r.stdout
    assert "`jm apply` writes the missing target_sources() line." in r.stdout
    assert "writes no line" not in r.stdout, r.stdout
    assert _cli("apply", cwd=project).returncode == 0
    assert _cli("status", "--check", cwd=project).returncode == 0


def test_mixed_names_which_core_apply_wires(project: Path):
    _no_generate_module(project)
    _unwire(project, "g_core")
    r = _cli("status", cwd=project)
    assert "UNWIRED (2)" in r.stdout, r.stdout
    assert (
        "`jm apply` writes the missing target_sources() line for g_core."
        in r.stdout
    ), r.stdout
    assert "`jm apply` writes no line for timing_obj" in r.stdout, r.stdout


def test_a_core_apply_cannot_place_points_at_the_anchor(tmp_path: Path):
    """gh-975's shape: jm's render has the line, but the root has lost the
    `# ── Modules` anchor, so apply cannot write it. Neither "apply writes
    it" nor "write it yourself" -- the anchor is the fix."""
    assert _cli("new", "p", "--module", "filt", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    assert (
        _cli(
            "object",
            "biq",
            "--module",
            "filt",
            "--state",
            "g:double:1.0",
            cwd=root,
        ).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    p = root / "CMakeLists.txt"
    body = p.read_text(encoding="utf-8")
    m = re.search(r"\n# ── Modules.*?\n# ─+\n", body, re.S)
    assert m
    p.write_text(body.replace(m.group(0), "\n"), encoding="utf-8")
    r = _cli("status", cwd=root)
    assert "UNANCHORED" in r.stdout, r.stdout
    assert "filt_core" in r.stdout, r.stdout
    assert "`jm apply` writes the missing" not in r.stdout, r.stdout
    assert "writes no line" not in r.stdout, r.stdout
    assert "renders the line for filt_core but cannot place it" in r.stdout


def test_json_says_whether_apply_wires_it(project: Path):
    _no_generate_module(project)
    _unwire(project, "g_core")
    payload = json.loads(_cli("status", "--json", cwd=project).stdout)
    got = {u["core"]: u["apply_wires"] for u in payload["unwired_cores"]}
    assert got == {"g_core": True, "timing_obj": False}
