"""gh-814 — a generated project never hands interpreter choice to CMake.

`find_package(Python3 ... NumPy)` takes the numpy headers from whatever
interpreter CMake resolves. On any machine with more than one numpy — a
system `python3-numpy` plus a venv one, which `bootstrap.toml` actively creates —
the extension can compile against one and be imported under the other. That
surfaces at IMPORT time as an ABI error, arbitrarily far from the build.

So every cmake invocation the generated Makefile makes must name the
interpreter, and must never name it as the empty string: CMake reads an empty
`-DPython3_EXECUTABLE=` as "discover one yourself", which is the very thing
being prevented. `just-build` passed the raw `$(JUST_BUILDIT_PYTHON)`, which
is set by just-buildit and empty otherwise.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_PIN = re.compile(r"-DPython3_EXECUTABLE=(\S*)")


def _makefile(tmp_path: Path) -> str:
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root)
        object_run(
            root,
            "c",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
    return (root / "Makefile").read_text()


class TestEveryCmakeCallNamesAnInterpreter:
    def test_no_cmake_call_omits_the_pin(self, tmp_path):
        mk = _makefile(tmp_path)
        # Every `cmake -B` configure line must carry the flag. `cmake --build`
        # reuses the cache, so only configure sites need it.
        configures = [b for b in mk.split("cmake -B")[1:]]
        assert configures, "no cmake configure found; template shape changed"
        for block in configures:
            head = block[:400]
            assert "-DPython3_EXECUTABLE=" in head, (
                "a cmake configure with no interpreter lets CMake choose, "
                "which is gh-814:\n" + head
            )

    def test_none_of_them_can_expand_to_empty(self, tmp_path):
        """The actual gh-814 defect: `$(JUST_BUILDIT_PYTHON)` raw is empty
        unless just-buildit set it, and `-DPython3_EXECUTABLE=` is not a
        no-op — it is 'choose for me'."""
        mk = _makefile(tmp_path)
        # Recipe lines only. The comments deliberately quote both the safe
        # and the unsafe spelling, so scanning the whole file would match the
        # very prose that explains the rule.
        recipe = "\n".join(
            ln for ln in mk.splitlines() if not ln.lstrip().startswith("#")
        )
        found = _PIN.findall(recipe)
        assert found, "no interpreter pin found; template shape changed"
        for value in found:
            assert value == "$(PYTHON)", (
                f"interpreter pinned as {value!r}; only $(PYTHON) is safe — "
                "it prefers $(JUST_BUILDIT_PYTHON) and falls back to a real "
                "interpreter, so it is empty in neither case"
            )

    def test_an_unresolvable_interpreter_fails_at_parse_time(self, tmp_path):
        """Loud beats silent: if the fallbacks find nothing, the build must
        stop where the cause is visible."""
        mk = _makefile(tmp_path)
        assert "ifeq ($(strip $(PYTHON)),)" in mk
        assert "$(error" in mk
