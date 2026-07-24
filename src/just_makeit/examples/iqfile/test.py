"""End-to-end test: iqfile scaffold -> implement -> build -> round-trip demo.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/iqfile/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


def run(root: Path) -> None:
    from just_makeit._apply import run as apply_run
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._property import run as jm_property

    # 1. Scaffold project + conv module
    jm_new("iqfile", root / "iqfile", modules=["conv"])
    proj = root / "iqfile"

    # 2. Add converter objects
    jm_object(
        proj,
        "cf32_to_q15",
        "conv",
        [("scale", "float", "32767.0f")],
        arg_type="float _Complex",
        return_type="int32_t",
    )
    jm_object(
        proj,
        "q15_to_cf32",
        "conv",
        [("fd", "int32_t", "-1"), ("scale", "float", "32767.0f")],
        arg_type="void",
        return_type="float _Complex",
    )

    # 3. Add properties
    #
    # Field-backed props carry their docstring in the manifest `--doc` value:
    # a `--field` getter is auto-implemented inline, so there is NO header
    # declaration for jm to hang a Doxygen `@brief` on. The manifest is the
    # single source of truth for these (mirrors the views_module finding).
    # The computed `eof` getter DOES have a header declaration, so its doc is
    # authored in C via `@brief` (see step 5b, 04b_doxygen.py).
    jm_property(
        proj,
        "cf32_to_q15",
        "samples_written",
        "conv",
        "uint32_t",
        False,
        field=True,
        doc="Total complex samples written since construction.",
    )
    jm_property(
        proj,
        "q15_to_cf32",
        "samples_read",
        "conv",
        "uint32_t",
        False,
        field=True,
        doc="Total complex samples read since construction.",
    )
    jm_property(proj, "q15_to_cf32", "eof", "conv", "int32_t", False)

    # 4. Implement C kernels
    _cmd([sys.executable, str(STEPS / "04_patch_writer.py")], cwd=proj)
    _cmd([sys.executable, str(STEPS / "04_patch_reader.py")], cwd=proj)

    # 5. Enrich the headers with Doxygen, regenerate the stubs.
    # The sacred header is the single source of truth for docs: a hand-written
    # @brief on each <obj>_create() becomes the class summary, and the computed
    # `eof` getter's @brief becomes that property's docstring. `jm apply`
    # re-derives the glue (.pyi included) from the edited headers. iqfile has no
    # named jm method, so there is no runnable-method @code doctest to run
    # (a property getter renders as prose only) — this is a "light" enrichment:
    # rich class summaries + property docs, verified below by asserting the
    # enriched prose landed in the stub.
    _cmd([sys.executable, str(STEPS / "04b_doxygen.py")], cwd=proj)
    apply_run(proj)

    # 5. CMake configure + build + CTest
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 7. Round-trip demo (module objects don't generate Python tests; demo is our integration check)
    _cmd([sys.executable, str(STEPS / "07_demo.py")], cwd=proj)

    # 8. Verify module-level type stub (named conv.pyi, not __init__.pyi)
    pyi = (proj / "src" / "iqfile" / "conv" / "conv.pyi").read_text()
    assert pyi.startswith("# conv/conv.pyi")
    assert "class Cf32ToQ15:" in pyi
    assert "class Q15ToCf32:" in pyi
    assert "import numpy as np" in pyi

    # The Doxygen enrichment (step 5b) reached the stub. Class summaries come
    # from each <obj>_create()'s @brief; the computed `eof` property doc from
    # its getter's @brief; the field-backed counter docs from the manifest
    # `--doc` value. No named method exists, so no runnable @code doctest.
    assert "Pack complex float samples into interleaved q15" in pyi, (
        "Cf32ToQ15 class @brief missing"
    )
    assert (
        "Read interleaved q15 (int16 I/Q) samples as complex float" in pyi
    ), "Q15ToCf32 class @brief missing"
    # Computed property doc, authored in C via the getter's @brief.
    assert (
        '"""True (1) once the backing file descriptor is exhausted."""' in pyi
    ), "eof computed-property @brief missing from stub"
    # Field-backed property docs, authored once via the manifest `--doc`.
    assert '"""Total complex samples written since construction."""' in pyi, (
        "samples_written field-property doc missing from stub"
    )
    assert '"""Total complex samples read since construction."""' in pyi, (
        "samples_read field-property doc missing from stub"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("iqfile: PASSED")
