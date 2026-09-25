"""gh-1109: the generated pytest attempts the seeded construction.

A `required` init-param with no default has no value jm can invent but the
type's zero, and a *validating* constructor rejects that. jm's answer was to
suppress the generated construction outright — decided at render time, from
the declaration alone.

The premise was weaker than it looked. jm never reads `_core.c`, deliberately,
but jm *wrote* that `_core.c`, and its scaffolded `create()` ignores the
parameter and never validates. So on day one the construction works and the
whole suite skips anyway.

The issue framed the alternative as reading `_core.c` to find out, and
rejected it for three reasons: jm does not read C; the generated test files
are create-only, so the answer would be frozen at scaffold time; and an
inference that goes stale trades a skip for a red suite. All three are about
deciding at RENDER time — and the C smoke generated beside these files has
always decided at *runtime*, making the zero-seeded call and treating NULL as
a skip. That was the option the Python side was missing, not one it had
weighed. It is now what the Python side does too.

Both directions are exercised against a real built extension, because the
question is entirely about what a C constructor does with a zero and no text
assertion can answer it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli


_SUMMARY = re.compile(r"^=+ (.*?) =+$", re.M)
_COUNT = re.compile(r"(\d+) (passed|skipped|failed|error|errors)")
#: An SGR escape, e.g. `\x1b[32m`. Stripped before `_SUMMARY` anchors
#: (gh-1456): under FORCE_COLOR the child pytest's summary line STARTS with
#: `\x1b[32m` and ENDS with `\x1b[0m`, so `^=+ ... =+$` matched no line,
#: every count came back 0, and a child reporting `8 passed` read as none.
#: `_COUNT` alone would have survived -- the escapes never split a number
#: from its word -- so the failure was the anchor, not the count.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _pytest_counts(stdout: str) -> "dict[str, int]":
    """Counts from pytest's own summary line in a `jm test` run.

    Anchored on that line rather than searched for in the whole output, and
    the reason is a bug this very test hit: `jm test` runs CTest first, whose
    output contains ``0 tests failed out of 1``, so a plain
    ``"failed" not in stdout`` is true of no run that has ever happened.
    """
    lines = _SUMMARY.findall(_ANSI.sub("", stdout))
    body = lines[-1] if lines else ""
    return {kind: int(n) for n, kind in _COUNT.findall(body)}


_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


class TestRenderedProbe:
    """What lands in the file, for the two shapes that differ."""

    @pytest.fixture
    def rendered(self, tmp_path: Path) -> str:
        assert _cli("new", "sd", "--no-c-prefix", cwd=tmp_path).returncode == 0
        root = tmp_path / "sd"
        assert (
            _cli(
                "object",
                "alloc",
                "--init-param",
                "capacity:size_t:required",
                cwd=root,
            ).returncode
            == 0
        )
        return (root / "src" / "sd" / "tests" / "test_alloc.py").read_text(
            encoding="utf-8"
        )

    def test_the_construction_is_attempted_not_assumed(
        self, rendered: str
    ) -> None:
        """The old form skipped in `setUp` without ever making the call."""
        assert "Alloc(capacity=0)\n        except TypeError" in rendered
        assert "def setUpClass(cls):" in rendered
        assert 'self.skipTest("required constructor' not in rendered

    def test_a_type_error_is_not_swallowed(self, rendered: str) -> None:
        """A validating constructor raises about a VALUE. A `TypeError` means
        jm seeded a call its own generated signature does not accept, which is
        a generator bug — catching it would turn every such bug into a green
        run reporting a skip."""
        assert "except TypeError:\n            raise" in rendered

    def test_the_reason_carries_the_constructors_own_message(
        self, rendered: str
    ) -> None:
        """A bare "seed valid arguments" note cannot tell an author whether
        their constructor rejected the seed or something else went wrong."""
        assert 'cls._jm_skip = "required constructor' in rendered
        assert '" + str(exc)' in rendered


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestAgainstARealExtension:
    """The only question a text assertion cannot answer."""

    @pytest.fixture
    def scaffolded(self, tmp_path: Path) -> Path:
        assert _cli("new", "sd", "--no-c-prefix", cwd=tmp_path).returncode == 0
        root = tmp_path / "sd"
        assert (
            _cli(
                "object",
                "alloc",
                "--init-param",
                "capacity:size_t:required",
                cwd=root,
            ).returncode
            == 0
        )
        return root

    def _run(self, root: Path) -> subprocess.CompletedProcess:
        """`jm test` — cmake configure + build, then CTest and pytest.

        Deliberately NOT `jm build`, which goes on to package a wheel: on
        macOS that ends in `delocate-wheel`, which fails on the runner for
        reasons that have nothing to do with this test, having compiled the
        extension cleanly moments earlier. `jm test` stops at the extension —
        and it is also the interface an author actually uses to ask the
        question these tests ask.
        """
        return _cli("test", cwd=root)

    def test_the_scaffolded_ctor_lets_the_suite_run(
        self, scaffolded: Path
    ) -> None:
        """jm's own `create()` ignores the parameter, so the zero seed is
        fine and there is nothing to skip. This is the case the render-time
        suppression got wrong on every project, on day one."""
        out = self._run(scaffolded)
        assert out.returncode == 0, out.stdout
        counts = _pytest_counts(out.stdout)
        assert counts.get("passed", 0) > 0, out.stdout
        assert counts.get("skipped", 0) == 0, out.stdout

    def test_validation_added_LATER_skips_rather_than_fails(
        self, scaffolded: Path
    ) -> None:
        """The objection that decided gh-1109 against a render-time
        inference: the test files are create-only, so an answer baked in at
        scaffold time is wrong the moment the author adds a check (gh-1088,
        red on `main` for 14 runs).

        The probe is not baked in. This file was generated BEFORE the
        validation below existed, and still degrades to a clean skip.
        """
        core = scaffolded / "native" / "src" / "alloc" / "alloc_core.c"
        body = core.read_text(encoding="utf-8")
        anchor = "    alloc_state_t *obj = calloc(1, sizeof(*obj));"
        assert anchor in body
        core.write_text(
            body.replace(
                anchor,
                "    if (capacity == 0)\n        return NULL;\n" + anchor,
            ),
            encoding="utf-8",
        )
        out = self._run(scaffolded)
        assert out.returncode == 0, out.stdout
        counts = _pytest_counts(out.stdout)
        assert counts.get("skipped", 0) > 0, out.stdout
        assert counts.get("failed", 0) == 0, out.stdout


class TestTheSummaryParserIgnoresColour:
    """gh-1456: a developer's `FORCE_COLOR` made this file fail while the
    project it checks was green.

    Asserted on the parser directly, with the exact bytes pytest emits
    under `FORCE_COLOR`, so it does not depend on the environment the suite
    happens to run in -- which is how it hid: CI sets no colour, so CI was
    green throughout.
    """

    COLOURED = (
        "\x1b[32m============================== \x1b[32m\x1b[1m8 passed"
        "\x1b[0m\x1b[32m in 0.06s\x1b[0m\x1b[32m ===============================\x1b[0m\n"
    )

    def test_a_coloured_summary_is_counted(self):
        assert _pytest_counts(self.COLOURED) == {"passed": 8}

    def test_a_plain_summary_still_is(self):
        plain = "=========== 3 passed, 1 skipped in 0.1s ===========\n"
        assert _pytest_counts(plain) == {"passed": 3, "skipped": 1}
