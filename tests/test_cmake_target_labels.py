"""The one-line labels above a component's C test and benchmark target.

The generated CMakeLists read as a tutorial: each target says what it is.
The component templates spell the labels, and `_render.TEST_LABEL` /
`BENCH_LABEL` are the same lines for the targets jm writes in Python (a
module's) and for `apply`, which inserts a component's extra include
directories just before its test target. Inserting before the bare
`add_executable(` line would separate the label from the target it names.

GATE: the templates and the Python emitter use one spelling, and after
`apply` re-renders a component with extra include directories, every label
is still the line right above its target.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli
from just_makeit import _render as R

TEMPLATES = Path(R.__file__).parent / "templates" / "cmake"


def test_the_templates_spell_the_labels_the_emitter_uses():
    for name in ("CMakeLists_component.cmake", "CMakeLists_object_core.cmake"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert R.TEST_LABEL in text, name
        assert R.BENCH_LABEL in text, name


def _labels_sit_on_their_targets(text: str) -> None:
    for label, kind in ((R.TEST_LABEL, "test"), (R.BENCH_LABEL, "bench")):
        targets = re.findall(rf"^add_executable\(\s*{kind}_\w+", text, re.M)
        assert targets, (kind, text)
        for m in re.finditer(rf"^add_executable\(\s*{kind}_\w+", text, re.M):
            above = text[: m.start()].splitlines(keepends=True)[-1]
            assert above == label, (kind, above, text)


def test_apply_keeps_each_label_on_its_target(tmp_path):
    """A module member's own CMakeLists is re-rendered by `apply`, which puts
    its extra include directories back just before the test target -- the
    one place jm inserts beside a label. The module's targets are the ones
    jm writes in Python."""
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    r = run_cli("module", "m", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "object",
        "h",
        "--module",
        "m",
        "--extra-include-dirs",
        "/opt/vendor/include",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "function",
        "clamp",
        "--module",
        "m",
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    member = (root / "native/src/h/CMakeLists.txt").read_text(encoding="utf-8")
    assert "/opt/vendor/include" in member, member
    _labels_sit_on_their_targets(member)
    module = (root / "native/src/m/CMakeLists.txt").read_text(encoding="utf-8")
    _labels_sit_on_their_targets(module)


def test_putting_include_dirs_back_keeps_the_label_on_its_target():
    """The `apply` reconcile restores a member's extra include directories
    before its test target when the re-render lacks them; the label stays
    on the target, above the restored block."""
    from just_makeit._apply import _reconciled_object_core_cmake

    rendered = (
        "add_library(h_core OBJECT h_core.c)\n\n"
        + R.TEST_LABEL
        + "add_executable(test_h_core t.c)\n"
    )
    out = _reconciled_object_core_cmake(
        rendered, rendered, "h", ["/opt/vendor/include"]
    )
    assert "/opt/vendor/include" in out, out
    assert R.TEST_LABEL + "add_executable(test_h_core" in out, out
