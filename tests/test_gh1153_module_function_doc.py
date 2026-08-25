"""gh-1153: a `[[module.X.functions]]` doc survives the manifest round-trip.

Adding a multi-paragraph `doc` to a module function made `jm apply` fail on
its own re-serialisation:

    error: just-makeit generated a manifest it cannot read back:
           Illegal character '\\n' (at line 904, column 237)

gh-844 collapsed four hand-rolled TOML escapers into one and wired the object,
method and property `doc` paths through it. The `[[module.X.functions]]`
dumper was a **fifth** and was missed, so it interpolated the value straight
into `doc = "..."`. A newline is illegal in a TOML basic string and so is an
unescaped quote, so a module function's doc was the one prose value in the
manifest that could hold neither.

Nothing was corrupted: `_dump` self-checks with `tomllib` and refuses to
write. So it presented as jm rejecting a manifest it had just produced, which
is a good failure and an unhelpful one.

**What this fixes is the manifest, and only the manifest.** The issue's
motivation was carrying a numpy-style `Parameters`/`Returns`/`Examples` block
so a docstring-coverage gate scores the surface complete, and that does NOT
work yet — see gh-1154. Measured here so the limit is recorded rather than
assumed: the value round-trips byte-identically, and the generated stub still
shows only its summary line.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

MULTI = "First paragraph.\n\nSecond paragraph."
QUOTED = 'say "hi" now'


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
    """A module function beside an object with a method and a property.

    The three neighbours are the point: they already round-tripped, because
    their dumpers were wired through gh-844's writer. Keeping them in the
    fixture is what makes this a test about the missed peer rather than about
    docs in general — if a later change breaks them instead, that shows up
    here too.
    """
    assert _cli("new", "pp", cwd=tmp_path).returncode == 0
    root = tmp_path / "pp"
    assert (
        _cli(
            "object", "eng", "--state", "gain:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert (
        _cli(
            "method",
            "eng",
            "exec",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            cwd=root,
        ).returncode
        == 0
    )
    assert _cli("module", "dsp", cwd=root).returncode == 0
    assert (
        _cli(
            "function",
            "fmap",
            "--module",
            "dsp",
            "--param",
            "b:int",
            "--return-type",
            "int",
            cwd=root,
        ).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    return root


def _set_doc(root: Path, rel: str, anchor: str, toml_value: str) -> None:
    p = root / rel
    body = p.read_text(encoding="utf-8")
    assert anchor in body, body
    p.write_text(
        body.replace(anchor, f"{anchor}\ndoc = {toml_value}", 1), "utf-8"
    )


def _loaded_doc(root: Path, kind: str) -> "str | None":
    from just_makeit import _config as C

    cfg = C.load(root)
    if kind == "function":
        return cfg["module"]["dsp"]["functions"][0].get("doc")
    return cfg["eng"]["methods"][0].get("doc")


class TestTheManifestRoundTrip:
    @pytest.mark.parametrize(
        "label,toml_value,expected",
        [
            (
                "multiline",
                '"""First paragraph.\n\nSecond paragraph."""',
                MULTI,
            ),
            ("quoted", '"say \\"hi\\" now"', QUOTED),
            ("plain", '"a plain summary"', "a plain summary"),
        ],
    )
    def test_a_module_function_doc_survives_apply(
        self, project: Path, label: str, toml_value: str, expected: str
    ) -> None:
        """Both spellings the issue reports, plus the single-line case that
        always worked — so a fix that broke the easy one cannot pass."""
        _set_doc(project, "modules/dsp.toml", 'name = "fmap"', toml_value)
        out = _cli("apply", cwd=project)
        assert "cannot read back" not in out.stdout + out.stderr, out.stdout
        assert out.returncode == 0, out.stdout
        assert _loaded_doc(project, "function") == expected

    def test_apply_is_idempotent_on_a_multiline_doc(
        self, project: Path
    ) -> None:
        """The value is re-dumped from the parsed form every run, so a writer
        that round-trips once but re-escapes on each pass would drift."""
        _set_doc(
            project,
            "modules/dsp.toml",
            'name = "fmap"',
            '"""First paragraph.\n\nSecond paragraph."""',
        )
        for _ in range(3):
            assert _cli("apply", cwd=project).returncode == 0
        assert _loaded_doc(project, "function") == MULTI

    def test_the_neighbours_that_already_worked_still_do(
        self, project: Path
    ) -> None:
        """gh-844's writer serves the method path; this pins it, so the fix
        cannot be 'made functions work by breaking methods'."""
        _set_doc(
            project,
            "objects/eng.toml",
            'name = "exec"',
            '"""First paragraph.\n\nSecond paragraph."""',
        )
        out = _cli("apply", cwd=project)
        assert "cannot read back" not in out.stdout + out.stderr
        assert _loaded_doc(project, "method") == MULTI


class TestWhatThisDoesNotFix:
    """The limit, recorded rather than assumed — see gh-1154.

    The issue's motivation was a numpy block reaching the generated surface.
    The manifest can now carry one; the renderer still cannot use it. Writing
    that down as a passing test means the day it changes, this fails and
    someone updates the claim instead of discovering it in a downstream.
    """

    def test_the_generated_stub_still_shows_only_the_summary(
        self, project: Path
    ) -> None:
        _set_doc(
            project,
            "modules/dsp.toml",
            'name = "fmap"',
            '"""Map a bin.\n\nExamples\n--------\n>>> fmap(3)\n3"""',
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src" / "pp" / "dsp" / "dsp.pyi").read_text("utf-8")
        assert "def fmap" in pyi
        assert "Map a bin." in pyi
        assert ">>> fmap(3)" not in pyi, (
            "the renderer now carries the body — update gh-1154 and this test"
        )
