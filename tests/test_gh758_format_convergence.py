"""gh-758: the formatter runs to a fixed point, so pass count cannot drift.

doppler enabled ``c_style = "clang-format"`` and got 12 ``*_ext.c`` module
aggregators that were permanently STALE: ``jm apply`` wrote them, ``jm status``
disagreed by exactly one column of continuation indent, and re-running
``apply`` never cleared it.

The reported cause — "status is not reproducing apply's clang-format on the
re-injected module docstring" — pointed at the wrong layer; jm emits ``.m_doc``
as a single long literal and performs no re-injection. The real mechanism is
two facts multiplying:

1. **clang-format is not idempotent on that construct.** Splitting the long
   ``.m_doc`` literal drops it out of the ``AlignConsecutiveAssignments``
   group, so the next pass realigns ``.m_size``/``.m_methods`` and shifts the
   string continuation by one column. Pass 2 is the fixed point, not pass 1.
   Reproduced on clang-format 22.1.8 with ``ColumnLimit: 79`` +
   ``AlignConsecutiveAssignments: Consecutive``.

2. **jm's paths did not agree on how many passes they run.** A real command
   formats twice — `_apply.run` formats its temp scaffold, then the CLI
   post-command hook (`_cli`) formats the real tree. `jm status` calls
   `_apply.run` on its scratch copy directly, bypassing that hook, so it
   formatted once. One pass versus two, on a construct where those differ.

Fixing the pass counts to match would leave the same trap armed for the next
caller. `_cfmt.format_project` instead converges, which makes the bytes
canonical: formatting an already-formatted tree is a genuine no-op, so *any*
number of passes lands in the same place.

The unit tests below use a scripted stand-in formatter so the invariant is
pinned on every machine, not just those with a non-idempotent clang-format.
The end-to-end test uses the real binary and is skipped without it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cfmt  # noqa: E402
from just_makeit import _cli  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_HAS_CLANG_FORMAT = shutil.which("clang-format") is not None

# A formatter that needs two passes, exactly like clang-format on an aligned
# multi-line `.m_doc`: PASS0 -> PASS1 -> PASS2, then stable forever.
_SETTLES_IN_TWO = """\
import sys
for p in sys.argv[1:]:
    if p.startswith("-"):
        continue
    t = open(p).read()
    if "PASS1" in t:
        t = t.replace("PASS1", "PASS2")
    elif "PASS0" in t:
        t = t.replace("PASS0", "PASS1")
    open(p, "w").write(t)
"""

# A formatter that never settles — it flips the marker back and forth.
_OSCILLATES = """\
import sys
for p in sys.argv[1:]:
    if p.startswith("-"):
        continue
    t = open(p).read()
    t = t.replace("PING", "\\x00") .replace("PONG", "PING").replace("\\x00", "PONG")
    open(p, "w").write(t)
"""


def _project_with_formatter(root: Path, script: str, body: str) -> dict:
    """A one-object project whose formatter is *script* and glue is *body*.

    Returns the loaded config with ``c_format_command`` pointed at the script,
    ready to hand to `_cfmt.format_project`.
    """
    new_run("proj", root, c_style="clang-format")
    object_run(root, "widget", None, state_vars=[("gain", "double", "1.0")])
    fmt = root / "fake_formatter.py"
    fmt.write_text(script)
    cfg = C.load(root)
    cfg["project"]["c_format_command"] = [sys.executable, str(fmt)]
    # The glue file is the only thing _generated_c_files sweeps.
    ext = root / "native" / "src" / "widget" / "widget_ext.c"
    ext.write_text(body)
    return cfg


class TestConvergence:
    """format_project keeps going until the bytes stop moving."""

    def test_runs_until_the_formatter_stops_changing_the_file(self, tmp_path):
        root = tmp_path / "proj"
        cfg = _project_with_formatter(
            root, _SETTLES_IN_TWO, "/* PASS0 */\nint x;\n"
        )

        _cfmt.format_project(root, cfg, quiet=True)

        ext = root / "native" / "src" / "widget" / "widget_ext.c"
        # Pre-gh-758 this stopped at PASS1 — the value a *second* caller
        # (the CLI hook) would then move to PASS2, which is the whole bug.
        assert "PASS2" in ext.read_text()

    def test_formatting_an_already_formatted_tree_is_a_no_op(self, tmp_path):
        """The property that makes pass count stop mattering."""
        root = tmp_path / "proj"
        cfg = _project_with_formatter(
            root, _SETTLES_IN_TWO, "/* PASS0 */\nint x;\n"
        )
        ext = root / "native" / "src" / "widget" / "widget_ext.c"

        _cfmt.format_project(root, cfg, quiet=True)
        once = ext.read_bytes()
        _cfmt.format_project(root, cfg, quiet=True)

        assert ext.read_bytes() == once

    def test_an_oscillating_formatter_warns_instead_of_hanging(
        self, tmp_path, capsys
    ):
        root = tmp_path / "proj"
        cfg = _project_with_formatter(
            root, _OSCILLATES, "/* PING */\nint x;\n"
        )

        _cfmt.format_project(root, cfg, quiet=True)

        err = capsys.readouterr().err
        assert "did not converge" in err
        # Naming the file is the point: an unexplained STALE entry is what
        # sent gh-758 looking at the wrong layer for a day.
        assert "widget_ext.c" in err


@pytest.mark.skipif(not _HAS_CLANG_FORMAT, reason="clang-format not installed")
class TestRealClangFormat:
    """The doppler construct, end to end, with the real binary."""

    # doppler's own settings, reduced to the two that matter here.
    _STYLE = (
        "BasedOnStyle: LLVM\n"
        "ColumnLimit: 79\n"
        "AlignConsecutiveAssignments: Consecutive\n"
    )

    # Long enough that clang-format must split it — the trigger condition.
    _DOC = (
        "Bit-error-rate measurement: a BerMeter that aligns a recovered bit "
        "stream to a reference and scores errors."
    )

    def test_apply_then_status_is_clean_for_a_wrapping_module_doc(
        self, tmp_path, capsys, monkeypatch
    ):
        """The doppler symptom, reproduced.

        `jm apply` must run through `_cli.main`, not `_apply.run`: ``apply``
        is in ``_C_EMITTING_COMMANDS``, so the CLI adds the post-command
        format pass over the real tree that `jm status`'s replay never runs.
        Calling `_apply.run` directly formats both sides once and the bug
        cannot appear — which is exactly why it stayed invisible.
        """
        root = tmp_path / "proj"
        new_run("proj", root, c_style="clang-format")
        (root / ".clang-format").write_text(self._STYLE)
        # The module docstring is the trigger: it lands in the aligned
        # PyModuleDef initializer as one long literal for clang-format to
        # split.
        module_run(root, "ber", doc=self._DOC)
        object_run(root, "ber", "ber", state_vars=[("gain", "double", "1.0")])

        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "argv", ["just-makeit", "apply"])
        _cli.main()
        capsys.readouterr()

        # The reported symptom: apply converges, yet status still says stale.
        assert _status.run(root, check=True) == 0

    def test_the_construct_really_does_need_two_passes(self, tmp_path):
        """Guards the premise — if clang-format ever becomes idempotent here,
        this test fails and the convergence loop is merely belt-and-braces
        rather than load-bearing. Either way it should be known, not assumed.
        """
        style = tmp_path / ".clang-format"
        style.write_text(self._STYLE)
        src = tmp_path / "m.c"
        src.write_text(
            "static struct PyModuleDef bermodule = {\n"
            "    PyModuleDef_HEAD_INIT,\n"
            '    .m_name    = "ber",\n'
            f'    .m_doc     = "{self._DOC}\\n",\n'
            "    .m_size    = -1,\n"
            "    .m_methods = ber_module_methods,\n"
            "};\n"
        )
        argv = ["clang-format", "-i", "--style=file", str(src)]

        subprocess.run(argv, check=True, cwd=tmp_path)
        first = src.read_bytes()
        subprocess.run(argv, check=True, cwd=tmp_path)
        second = src.read_bytes()
        subprocess.run(argv, check=True, cwd=tmp_path)

        if first == second:
            pytest.skip("this clang-format is idempotent on the construct")
        # Two passes is the fixed point; a third must change nothing.
        assert src.read_bytes() == second


# A command whose resolved "version" depends on the directory it runs in —
# the shape of `uv run --group dev clang-format`, which no-ops outside a
# project and falls through to PATH.
_CWD_SENSITIVE = """\
import os
import sys
here = os.path.basename(os.getcwd())
print("clang-format version 22.1.8" if here == "proj"
      else "clang-format version 21.1.8")
"""

# Same shape, but stable — proves the check does not fire on every project.
_CWD_STABLE = 'print("clang-format version 22.1.8")\n'


class TestCwdDependentFormatter:
    """gh-758's other half: the misconfiguration that started the report.

    doppler pinned `["uv","run","--group","dev","clang-format"]`, which is
    silently a no-op outside a project. `apply` formats its temp scaffold from
    outside, so the two compared sides met two different binaries. jm can see
    that directly by asking the command its version from two directories.
    """

    def _cfg_with(self, root: Path, script: str) -> dict:
        new_run("proj", root, c_style="clang-format")
        probe = root / "probe.py"
        probe.write_text(script)
        cfg = C.load(root)
        cfg["project"]["c_format_command"] = [sys.executable, str(probe)]
        return cfg

    def test_detects_a_command_that_resolves_two_binaries(self, tmp_path):
        root = tmp_path / "proj"
        cfg = self._cfg_with(root, _CWD_SENSITIVE)

        found = _cfmt.cwd_dependent_version(root, cfg)

        assert found is not None
        here, there = found
        assert "22.1.8" in here
        assert "21.1.8" in there

    def test_stays_silent_for_a_cwd_independent_command(self, tmp_path):
        root = tmp_path / "proj"
        cfg = self._cfg_with(root, _CWD_STABLE)

        assert _cfmt.cwd_dependent_version(root, cfg) is None

    def test_stays_silent_when_formatting_is_off(self, tmp_path):
        """gh-773 moved what "off" means: declaring `c_format_command` is
        itself the opt-in, so this fixture has to drop the command as well as
        the style. Setting `c_style = "none"` beside a declared command used
        to silence the check — which is precisely the silent no-op the
        one-predicate change removes."""
        root = tmp_path / "proj"
        cfg = self._cfg_with(root, _CWD_SENSITIVE)
        cfg["project"]["c_style"] = "none"
        cfg["project"].pop("c_format_command", None)

        assert _cfmt.cwd_dependent_version(root, cfg) is None

    def test_a_declared_command_is_checked_without_c_style(self, tmp_path):
        """The other half: the CWD-dependence warning now reaches a project
        that declared the command and never set `c_style` — the shape
        doppler#616 reported as getting nothing, silently."""
        root = tmp_path / "proj"
        cfg = self._cfg_with(root, _CWD_SENSITIVE)
        cfg["project"].pop("c_style", None)

        assert _cfmt.cwd_dependent_version(root, cfg) is not None

    def test_stays_silent_when_the_binary_is_missing(self, tmp_path):
        root = tmp_path / "proj"
        cfg = self._cfg_with(root, _CWD_STABLE)
        cfg["project"]["c_format_command"] = ["definitely-not-a-real-binary"]

        assert _cfmt.cwd_dependent_version(root, cfg) is None
