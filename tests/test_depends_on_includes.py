"""`depends_on` auto-includes the dependency's header (gh-170).

When a component declares ``depends_on = ["lfsr"]`` and uses the dependency's
types (e.g. an opaque ``lfsr_state_t *`` field), jm links `lfsr_core` AND now
injects ``#include "lfsr/lfsr_core.h"`` into the dependent's ``_core.h`` so it
compiles without a manual edit. Also covers the `mutable` synonym for `out` on
a module-function array param (the related const-vs-writable observation).
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._init import _inject_includes_into_core_h  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold_dep_pair(dest: Path):
    _silent(new_run, "tp", dest)
    _silent(
        object_run,
        dest,
        "lfsr",
        None,
        state_vars=[("seed", "uint32_t", "1")],
        arg_type="uint8_t",
        return_type="uint8_t",
    )
    _silent(
        object_run,
        dest,
        "wfm",
        None,
        no_state=True,
        no_step=True,
        init_params=[("n", "uint32_t", "8")],
    )
    cfg = C.load(dest)
    cfg["wfm"]["depends_on"] = ["lfsr"]
    C.save(dest, cfg)


# ── (a) apply injects the dependency include, idempotently ───────────────────
def test_apply_injects_depends_on_include(tmp_path):
    _scaffold_dep_pair(tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")
    hdr = tmp_path / "tp/native/inc/wfm/wfm_core.h"
    assert '#include "lfsr/lfsr_core.h"' in hdr.read_text(encoding="utf-8")
    _silent(apply_run, tmp_path / "tp")  # idempotent
    assert (
        hdr.read_text(encoding="utf-8").count('#include "lfsr/lfsr_core.h"')
        == 1
    )


# ── (b) the include sits among the other #includes, before the struct ────────
def test_include_placement(tmp_path):
    _scaffold_dep_pair(tmp_path / "tp")
    _silent(apply_run, tmp_path / "tp")
    text = (tmp_path / "tp/native/inc/wfm/wfm_core.h").read_text(
        encoding="utf-8"
    )
    assert text.index("clib_common.h") < text.index("lfsr/lfsr_core.h")
    # before the struct typedef (the doc comment mentions wfm_state_t earlier)
    assert text.index("lfsr/lfsr_core.h") < text.index("} wfm_state_t;")


# ── (c) mutable is a synonym for out on a module-function array param ─────────
def test_function_mutable_param_is_non_const(tmp_path):
    dest = tmp_path / "tp"
    _silent(new_run, "tp", dest, modules=["dsp"])
    cfg = C.load(dest)
    cfg["module"]["dsp"]["functions"] = [
        {
            "name": "process",
            "params": [
                {"name": "input", "type": "float[]"},
                {"name": "output", "type": "float[]", "mutable": True},
            ],
        }
    ]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    decl = next(
        line
        for line in (dest / "native/inc/dsp/dsp_core.h")
        .read_text(encoding="utf-8")
        .splitlines()
        if "process(" in line
    )
    assert "float *output" in decl  # writable
    assert "const float *output" not in decl
    assert "const float *input" in decl  # non-mutable stays const
    # `mutable` canonicalises to `out` on a re-dump (round-trip)
    assert "out = true" in C._dump(C.load(dest))


def _inc_layout(tmp_path: Path, comp: str, comp_body: str, deps=()) -> Path:
    """Write native/inc/<comp>/<comp>_core.h plus an empty header per dep, so
    the dep-header existence check resolves. Returns the component header."""
    inc = tmp_path / "native" / "inc"
    for d in deps:
        (inc / d).mkdir(parents=True, exist_ok=True)
        (inc / d / f"{d}_core.h").write_text("/* dep */\n", encoding="utf-8")
    h = inc / comp / f"{comp}_core.h"
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text(comp_body, encoding="utf-8")
    return h


# ── unit: the injector is idempotent and a no-op without deps ────────────────
def test_inject_includes_idempotent(tmp_path):
    hdr = _inc_layout(
        tmp_path,
        "x",
        "#ifndef X_CORE_H\n#define X_CORE_H\n"
        '#include "clib_common.h"\n\n'
        "typedef struct { int a; } x_state_t;\n"
        "#endif /* X_CORE_H */\n",
        deps=["dep"],
    )
    assert _inject_includes_into_core_h(hdr, "x", ["dep"]) is True
    assert '#include "dep/dep_core.h"' in hdr.read_text(encoding="utf-8")
    assert _inject_includes_into_core_h(hdr, "x", ["dep"]) is False  # no-op
    assert _inject_includes_into_core_h(hdr, "x", []) is False  # no deps


def test_inject_skips_dep_without_a_header(tmp_path):
    # A bare link-target dep (e.g. `lo_core`) has no `lo_core/lo_core_core.h`,
    # so no broken #include is injected (gh-170 follow-up).
    hdr = tmp_path / "x_core.h"
    hdr.write_text(
        "#ifndef X_CORE_H\n#define X_CORE_H\n"
        '#include "clib_common.h"\n\n'
        "typedef struct { int a; } x_state_t;\n"
        "#endif /* X_CORE_H */\n",
        encoding="utf-8",
    )
    # native/inc/<dep>/<dep>_core.h does not exist for these
    assert _inject_includes_into_core_h(hdr, "x", ["lo_core"]) is False
    assert "lo_core" not in hdr.read_text(encoding="utf-8")


def test_apply_skips_link_target_dep(tmp_path):
    # Full apply: a component depending on a bare link target gets no include.
    _silent(new_run, "tp", tmp_path / "tp")
    _silent(
        object_run,
        tmp_path / "tp",
        "wfm",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    cfg = C.load(tmp_path / "tp")
    cfg["wfm"]["depends_on"] = ["m", "some_core"]  # libm + a bare target
    C.save(tmp_path / "tp", cfg)
    _silent(apply_run, tmp_path / "tp")
    text = (tmp_path / "tp/native/inc/wfm/wfm_core.h").read_text(
        encoding="utf-8"
    )
    assert "some_core/some_core_core.h" not in text
    assert '#include "m/m_core.h"' not in text


def test_inject_includes_fallback_when_no_includes(tmp_path):
    # A header with no #include lines: insert before the extern "C" / guard.
    hdr = _inc_layout(
        tmp_path,
        "y",
        "#ifndef Y_CORE_H\n#define Y_CORE_H\n"
        '#ifdef __cplusplus\nextern "C" {\n#endif\n'
        "typedef struct { int a; } y_state_t;\n"
        "#endif /* Y_CORE_H */\n",
        deps=["dep"],
    )
    assert _inject_includes_into_core_h(hdr, "y", ["dep"]) is True
    text = hdr.read_text(encoding="utf-8")
    assert '#include "dep/dep_core.h"' in text
    assert text.index("dep/dep_core.h") < text.index("#ifdef __cplusplus")


# ── a module object's header also gets its depends_on include ────────────────
def test_module_object_gets_depends_on_include(tmp_path):
    from just_makeit._module import run as module_run

    dest = tmp_path / "tp"
    _silent(new_run, "tp", dest)
    _silent(module_run, dest, "sig")
    _silent(
        object_run,
        dest,
        "lfsr",
        None,
        state_vars=[("seed", "uint32_t", "1")],
        arg_type="uint8_t",
        return_type="uint8_t",
    )
    _silent(
        object_run,
        dest,
        "mix",
        module="sig",
        no_state=True,
        no_step=True,
        init_params=[("n", "uint32_t", "8")],
    )
    cfg = C.load(dest)
    cfg["mix"]["depends_on"] = ["lfsr"]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    hdr = (dest / "native/inc/mix/mix_core.h").read_text(encoding="utf-8")
    assert '#include "lfsr/lfsr_core.h"' in hdr


# ── gh-174 follow-up: a depends_on object's own test/bench link the dep core ──
def test_depends_on_links_dep_into_test_bench(tmp_path):
    from just_makeit._module import run as module_run

    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "dsp")
    _silent(
        object_run,
        dest,
        "osc",
        module="dsp",
        state_vars=[("ph", "uint32_t", "0")],
        arg_type="void",
        return_type="float",
    )
    _silent(
        object_run,
        dest,
        "mix",
        module="dsp",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        depends_on=["osc"],
    )
    cmake = (dest / "native/src/mix/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    # fresh creation: the dep core reaches the object lib (PUBLIC) AND its
    # test/bench exes — PUBLIC line + test + bench all name osc_core.
    assert "target_link_libraries(mix_core PUBLIC" in cmake
    assert "test_mix_core" in cmake
    assert cmake.count("osc_core") >= 3

    # apply-on-existing: depends_on added later still wires it (surgical
    # injector) — at least the PUBLIC line, which propagates to test/bench.
    _silent(
        object_run,
        dest,
        "solo",
        module="dsp",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    cfg = C.load(dest)
    cfg["solo"]["depends_on"] = ["osc"]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    solo = (dest / "native/src/solo/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "target_link_libraries(solo_core PUBLIC" in solo
    assert "osc_core" in solo
