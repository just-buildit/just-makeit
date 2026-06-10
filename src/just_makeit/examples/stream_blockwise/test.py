"""End-to-end test for the stream_blockwise example — keeps the README honest.

Runs the exact walkthrough the README teaches: scaffold the streamable drainer
(matching .steps/01_scaffold.sh), splice in run_max_out()/run() from
.steps/02_*.c, build, then run .steps/03_demo.py against the built extension.
The C functions and the Python demo are read from the same .steps/ files the
README embeds, so the taught code is literally the code that compiles and runs.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/stream_blockwise/test.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def _matching_brace(src: str, open_idx: int) -> int:
    """Index just past the '}' that matches the '{' at open_idx."""
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("unbalanced braces")


def _replace_function(src: str, name: str, replacement: str) -> str:
    """Replace the whole definition of C function ``name`` in ``src``.

    Finds ``name(`` whose matching ``)`` is followed by ``{`` (a definition,
    not a call or prototype), extends back over the return-type line, and swaps
    everything through the matching ``}`` for ``replacement``. A preceding
    ``/* <<IMPLEMENT ... >> */`` scaffold comment is dropped too.
    """
    pos = 0
    while True:
        idx = src.find(name + "(", pos)
        if idx == -1:
            raise AssertionError(f"definition of {name}() not found")
        depth, i = 0, idx + len(name)
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        j = i + 1
        while j < len(src) and src[j] in " \t\r\n":
            j += 1
        if j < len(src) and src[j] == "{":  # a definition
            line_start = src.rfind("\n", 0, idx) + 1
            ret_start = src.rfind("\n", 0, line_start - 1) + 1
            before = src[:ret_start].rstrip()
            if before.endswith("*/"):
                c_open = before.rfind("/*")
                if c_open != -1 and "<<IMPLEMENT" in before[c_open:]:
                    ret_start = c_open
            end = _matching_brace(src, j)
            return src[:ret_start] + replacement + src[end:]
        pos = idx + 1


def _splice(src: str, name: str, snippet_file: str) -> str:
    """Splice the complete function from .steps/<snippet_file> over ``name``.

    The snippet's leading ``/* Implement in ... */`` note is dropped — only the
    function (from its ``size_t`` return-type line) is spliced in.
    """
    snippet = (STEPS / snippet_file).read_text(encoding="utf-8")
    fn = snippet[snippet.index("size_t") :].rstrip() + "\n"
    return _replace_function(src, name, fn)


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object

    # 1. Scaffold — mirrors .steps/01_scaffold.sh. --variable-output adds the
    #    run(n) producer; --streamable picks it as the stream source.
    proj = root / "stream_blockwise_demo"
    jm_new("stream_blockwise_demo", proj)
    jm_object(
        proj,
        "drainer",
        module=None,
        arg_type="void",
        return_type="float _Complex",
        mutable=True,
        streamable=True,
        variable_output=True,
        state_vars=[("total", "int32_t", "20"), ("pos", "int32_t", "0")],
    )

    ext = (proj / "native" / "src" / "drainer" / "drainer_ext.c").read_text()
    assert "DrainerStreamIter" in ext
    assert "Drainer_stream" in ext
    # The producer is the variable_output method, not the built-in steps.
    assert 'PyObject_CallMethod(it->src, "run", "n"' in ext
    pyi = (proj / "src" / "stream_blockwise_demo" / "drainer.pyi").read_text()
    assert "def stream(" in pyi
    assert "def __iter__(self) -> Iterator[NDArray[np.complex64]]:" in pyi

    # 2. Splice in run_max_out() and run() — the SAME .steps/02_*.c the README
    #    embeds, so the taught functions are the compiled functions.
    core_c = proj / "native" / "src" / "drainer" / "drainer_core.c"
    text = core_c.read_text(encoding="utf-8")
    text = _splice(text, "drainer_run_max_out", "02_max_out.c")
    text = _splice(text, "drainer_run", "02_run.c")
    core_c.write_text(text, encoding="utf-8")

    # 3. Build (cmake + ctest runs the generated C smoke test).
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

    # 4. Run the README's demo verbatim against the freshly built extension.
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(proj / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    _cmd([sys.executable, str(STEPS / "03_demo.py")], cwd=proj, env=env)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("stream_blockwise: PASSED")
