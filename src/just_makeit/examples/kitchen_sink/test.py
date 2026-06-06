"""Kitchen-sink example: every jm feature exercised together.

The integration surface — vendored C deps, cross-component `depends_on`,
`nogil`, component-level `extra_link_libs`, every object flavor — only breaks
when combined in one project. This example builds exactly such a project so
jm's CI catches "all-together" regressions (it already surfaced gh-174's
`depends_on` test/bench link bug).

Generated project (module `dsp`):
  - gain   — scalar step(x)->y, writable property
  - lfo    — generator void->complex64, --class-name Lfo, --mutable
  - meter  — consumer float->void, --field property
  - resamp — variable_output + pass_capacity + nogil execute (decimate by 2)
  - mixer  — depends_on ["lfo"]: opaque sibling lfo_state_t* (header auto-incl)
  - config — vendored cJSON: opaque cJSON*, component extra_link_libs +
             extra_include_dirs (the gh-174 path)
  - cjson  — a [project] c_deps OBJECT lib (vendored, no Python wrapper)
  - tone   — (optional) links the real doppler: opaque nco_state_t*

Called by tests/test_examples.py via run(root). Skips cleanly if cmake / a C
compiler / numpy are unavailable (the shared harness checks those).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _cmd(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


# ── vendored cJSON-compatible reader (a [project] c_deps OBJECT lib) ──────────
_CJSON_H = """\
/* cJSON.h — compact cJSON-compatible JSON reader (vendored for the example).
 * A trimmed subset of the cJSON API: parse a flat object and read numbers.
 */
#ifndef CJSON_H
#define CJSON_H

typedef struct cJSON {
    char *key;
    double number;
    struct cJSON *next;
} cJSON;

cJSON *cJSON_Parse(const char *text);
cJSON *cJSON_GetObjectItem(const cJSON *obj, const char *key);
double cJSON_GetNumberValue(const cJSON *item);
void cJSON_Delete(cJSON *obj);

#endif /* CJSON_H */
"""

_CJSON_C = r"""/* Compact cJSON-compatible reader: flat {"k": <number>, ...} objects. */
#include "cJSON.h"
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static const char *skip_ws(const char *p) {
    while (*p && isspace((unsigned char)*p)) p++;
    return p;
}

cJSON *cJSON_Parse(const char *text) {
    const char *p = skip_ws(text);
    if (*p != '{') return NULL;
    p++;
    cJSON *head = NULL, *tail = NULL;
    p = skip_ws(p);
    while (*p && *p != '}') {
        p = skip_ws(p);
        if (*p != '"') { cJSON_Delete(head); return NULL; }
        const char *ks = ++p;
        while (*p && *p != '"') p++;
        if (*p != '"') { cJSON_Delete(head); return NULL; }
        size_t klen = (size_t)(p - ks);
        p = skip_ws(p + 1);
        if (*p != ':') { cJSON_Delete(head); return NULL; }
        p = skip_ws(p + 1);
        char *end = NULL;
        double val = strtod(p, &end);
        if (end == p) { cJSON_Delete(head); return NULL; }
        p = end;
        cJSON *node = calloc(1, sizeof(cJSON));
        if (!node) { cJSON_Delete(head); return NULL; }
        node->key = malloc(klen + 1);
        if (!node->key) { free(node); cJSON_Delete(head); return NULL; }
        memcpy(node->key, ks, klen);
        node->key[klen] = '\0';
        node->number = val;
        if (tail) tail->next = node; else head = node;
        tail = node;
        p = skip_ws(p);
        if (*p == ',') p = skip_ws(p + 1);
    }
    return head ? head : calloc(1, sizeof(cJSON));
}

cJSON *cJSON_GetObjectItem(const cJSON *obj, const char *key) {
    for (const cJSON *n = obj; n; n = n->next)
        if (n->key && strcmp(n->key, key) == 0) return (cJSON *)n;
    return NULL;
}

double cJSON_GetNumberValue(const cJSON *item) {
    return item ? item->number : 0.0;
}

void cJSON_Delete(cJSON *obj) {
    while (obj) { cJSON *nx = obj->next; free(obj->key); free(obj); obj = nx; }
}
"""

_CJSON_CMAKE = """\
# Vendored cJSON-compatible reader — pure C OBJECT lib, no Python wrapper.
add_library(cjson_core OBJECT cJSON.c)
target_include_directories(cjson_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
"""

# ── object fragments needing TOML-only keys (opaque, depends_on, component
#    extra_link_libs) — not expressible on the `jm object` CLI ────────────────
_MIXER_TOML = '''\
[mixer]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "true"
depends_on = ["lfo"]

create_impl = """
obj->osc = lfo_create(0, 858993459u);
if (!obj->osc) { free(obj); return NULL; }
"""
destroy_impl = """
lfo_destroy(state->osc);
"""

[[mixer.state]]
name = "osc"
type = "lfo_state_t *"
opaque = true
'''

_CONFIG_TOML = '''\
[config]
arg_type = "void"
return_type = "void"
mutable = "false"
no_step = "true"
extra_link_libs = ["cjson_core"]
extra_include_dirs = ["${CMAKE_SOURCE_DIR}/native/src/cjson"]

create_impl = """
obj->root = cJSON_Parse(json ? json : "{}");
if (!obj->root) { free(obj); return NULL; }
"""
destroy_impl = """
cJSON_Delete(state->root);
"""

[[config.init_params]]
name = "json"
type = "const char *"
default = "0"

[[config.state]]
name = "root"
type = "cJSON *"
opaque = true

[[config.methods]]
name = "get_number"
return_type = "double"
params = [{name = "key", type = "const char *"}]
'''


# ── linking the real doppler C library (opaque nco_state_t*), conditional ─────
# Reuses nco_tone's provisioning + skip harness rather than duplicating it.
_TONE_TOML = '''\
[tone]
arg_type     = "void"
return_type  = "float _Complex"
mutable      = "true"
extra_link_libs = ["doppler::doppler_lib"]
create_impl  = """
obj->nco = nco_create(norm_freq, 0);
if (!obj->nco) { free(obj); return NULL; }
"""
destroy_impl = """
nco_destroy(state->nco);
"""

[[tone.state]]
name    = "norm_freq"
type    = "double"
default = "0.0"

[[tone.state]]
name   = "nco"
type   = "nco_state_t *"
opaque = true
'''

_TONE_STEP_OLD = (
    "    (void)state; /* TODO: implement */\n    return (float complex)0;"
)
_TONE_STEP_NEW = """\
    uint32_t phase;
    nco_steps_u32(state->nco, 1, &phase);
    float angle = (float)phase
                  * (float)(2.0 * 3.14159265358979323846 / 4294967296.0);
    return cosf(angle) + I * sinf(angle);"""


def _doppler_prefix():
    """Find/provision the real doppler via nco_tone's harness (or None)."""
    import importlib.util

    p = HERE.parent / "nco_tone" / "test.py"
    spec = importlib.util.spec_from_file_location("_nco_tone_dop", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._find_doppler_prefix()


def _doppler_dir(prefix: str) -> str:
    prefix_path = Path(prefix)
    for rel in ("lib/cmake/doppler", "lib64/cmake/doppler", "."):
        if (prefix_path / rel / "doppler-config.cmake").exists():
            return str(prefix_path / rel)
    return prefix


def _patch(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if new.strip() and new in text:
        return  # already applied
    assert old in text, f"stub not found in {path}:\n{old}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _implement_c_bodies(proj: Path):
    """Fill the generated stubs with real DSP bodies (the only hand step)."""
    inc = proj / "native" / "inc"
    src = proj / "native" / "src"
    # gain — scalar
    _patch(
        inc / "gain" / "gain_core.h",
        "    (void)state; /* TODO: implement using state variables */\n"
        "    return (float)x;",
        "    return state->gain * x;",
    )
    # lfo — mutable generator (needs <math.h>)
    _patch(
        inc / "lfo" / "lfo_core.h",
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <math.h>',
    )
    _patch(
        inc / "lfo" / "lfo_core.h",
        "    (void)state; /* TODO: implement */\n    return (float complex)0;",
        "    state->phase += state->inc;\n"
        "    float a = (float)state->phase\n"
        "              * (float)(2.0 * 3.14159265358979323846\n"
        "                        / 4294967296.0);\n"
        "    return cosf(a) + I * sinf(a);",
    )
    # meter — consumer (needs <math.h>)
    _patch(
        inc / "meter" / "meter_core.h",
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <math.h>',
    )
    _patch(
        inc / "meter" / "meter_core.h",
        "    (void)state; (void)x; /* TODO: implement */",
        "    float m = fabsf(x);\n    if (m > state->peak)\n"
        "        state->peak = m;",
    )
    # mixer — uses the sibling lfo (via depends_on)
    _patch(
        inc / "mixer" / "mixer_core.h",
        "    (void)state; /* TODO: implement using state variables */\n"
        "    return (float complex)x;",
        "    return x * lfo_step(state->osc);",
    )
    # resamp — variable_output + pass_capacity + nogil (decimate by 2)
    _patch(
        src / "resamp" / "resamp_core.c",
        "    (void)state;\n"
        "    (void)in; (void)n_in;\n"
        "    (void)out; (void)max_out;\n"
        "    return 0; /* placeholder */",
        "    (void)state;\n    size_t n_out = 0;\n"
        "    for (size_t i = 0; i < n_in && n_out < max_out; i += 2)\n"
        "        out[n_out++] = in[i];\n    return n_out;",
    )
    # config — uses vendored cJSON; needs cJSON.h in its header
    _patch(
        inc / "config" / "config_core.h",
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include "cJSON.h"',
    )
    _patch(
        src / "config" / "config_core.c",
        "    (void)state; (void)key;\n    return (double)0.0;",
        "    cJSON *item = cJSON_GetObjectItem(state->root, key);\n"
        "    return cJSON_GetNumberValue(item);",
    )


def run(root: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._apply import run as jm_apply
    from just_makeit._method import run as jm_method
    from just_makeit._module import run as jm_module
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._property import run as jm_property

    def q(fn, *a, **k):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    proj = root / "kitchen_sink"
    q(jm_new, "kitchen_sink", proj, [], [], perf=True, fragments=True)
    q(jm_module, proj, "dsp")

    # scalar + writable property
    q(
        jm_object,
        proj,
        "gain",
        module="dsp",
        state_vars=[("gain", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    q(jm_property, proj, "gain", "gain", "dsp", "float", True)
    # generator — named `lfo`, not `nco`, to avoid clashing with the doppler
    # `nco` header that the optional `tone` object links below.
    q(
        jm_object,
        proj,
        "lfo",
        module="dsp",
        state_vars=[("phase", "uint32_t", "0"), ("inc", "uint32_t", "0")],
        arg_type="void",
        return_type="float _Complex",
        mutable=True,
        class_name="Lfo",
    )
    # consumer + field property
    q(
        jm_object,
        proj,
        "meter",
        module="dsp",
        state_vars=[("peak", "float", "0.0f")],
        arg_type="float",
        return_type="void",
    )
    q(jm_property, proj, "meter", "peak", "dsp", "float", False, field=True)
    # blockwise: variable_output + pass_capacity + nogil
    q(
        jm_object,
        proj,
        "resamp",
        module="dsp",
        state_vars=[("ratio", "double", "0.5")],
        arg_type="float _Complex",
        return_type="float _Complex",
        no_step=True,
    )
    q(
        jm_method,
        proj,
        "resamp",
        "execute",
        "dsp",  # module
        "float _Complex[]",  # arg_type
        "float _Complex",  # return_type
        True,  # variable_output
        [],  # multi_output
        pass_capacity=True,
        nogil=True,
    )

    # vendor cJSON under native/src/cjson (a [project] c_deps OBJECT lib)
    cjson = proj / "native" / "src" / "cjson"
    cjson.mkdir(parents=True, exist_ok=True)
    (cjson / "cJSON.h").write_text(_CJSON_H, encoding="utf-8")
    (cjson / "cJSON.c").write_text(_CJSON_C, encoding="utf-8")
    (cjson / "CMakeLists.txt").write_text(_CJSON_CMAKE, encoding="utf-8")

    # Optional: link the REAL doppler C library via a standalone `tone` object
    # with an opaque doppler nco_state_t*. Conditional on doppler being
    # available (reuses nco_tone's provisioning); skipped cleanly otherwise so
    # the rest of the example always runs.
    doppler_prefix = _doppler_prefix()
    if not doppler_prefix:
        print("kitchen_sink: doppler not found — skipping the `tone` object")

    # All [project] manifest edits go through C.save FIRST, before writing the
    # raw TOML fragments below — a C.save re-dumps every fragment, and an older
    # jm drops heredoc bodies (create_impl/…) on the round-trip.
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["cjson"]
    if doppler_prefix:
        cfg["project"]["find_packages"] = ["Doppler"]
    C.save(proj, cfg)

    # objects needing TOML-only keys (opaque, depends_on, component
    # extra_link_libs, create_impl) — written as raw fragments, last.
    (proj / "objects" / "mixer.toml").write_text(_MIXER_TOML, encoding="utf-8")
    (proj / "objects" / "config.toml").write_text(
        _CONFIG_TOML, encoding="utf-8"
    )
    objs = '["gain", "lfo", "meter", "resamp", "mixer", "config"]'
    if doppler_prefix:
        (proj / "objects" / "tone.toml").write_text(
            _TONE_TOML, encoding="utf-8"
        )
    mod = proj / "modules" / "dsp.toml"
    mod.write_text(
        mod.read_text(encoding="utf-8").replace(
            '["gain", "lfo", "meter", "resamp"]', objs
        ),
        encoding="utf-8",
    )

    q(jm_apply, proj)

    # A vendored c_dep that a component uses must also be folded into the
    # umbrella `<pkg>_lib` — `config_core` is added there (TARGET_OBJECTS) but
    # its cJSON symbols aren't, so the shared lib has undefined symbols (a hard
    # error on macOS, tolerated on Linux). Mirror how doppler folds its vendored
    # pocketfft into doppler_lib — the canonical vendored-dep pattern.
    top = proj / "CMakeLists.txt"
    top_text = top.read_text(encoding="utf-8")
    if "$<TARGET_OBJECTS:cjson_core>" not in top_text:
        top.write_text(
            top_text + "\ntarget_sources(kitchen_sink_lib PRIVATE"
            " $<TARGET_OBJECTS:cjson_core>)\n"
            "target_sources(kitchen_sink_lib_static PRIVATE"
            " $<TARGET_OBJECTS:cjson_core>)\n",
            encoding="utf-8",
        )

    # the only hand step: implement the C algorithm bodies
    _implement_c_bodies(proj)

    if doppler_prefix:
        tone_h = proj / "native/inc/tone/tone_core.h"
        _patch(
            tone_h,
            '#include "clib_common.h"',
            '#include "clib_common.h"\n#include "nco/nco_core.h"\n'
            "#include <math.h>",
        )
        _patch(tone_h, _TONE_STEP_OLD, _TONE_STEP_NEW)
        tone_cmake = (proj / "native/src/tone/CMakeLists.txt").read_text(
            "utf-8"
        )
        assert "doppler::doppler_lib" in tone_cmake  # links the real doppler

    # assert the integration wiring jm produced ----------------------------
    mixer_h = (proj / "native/inc/mixer/mixer_core.h").read_text("utf-8")
    assert '#include "lfo/lfo_core.h"' in mixer_h  # depends_on auto-include
    mixer_cmake = (proj / "native/src/mixer/CMakeLists.txt").read_text("utf-8")
    assert mixer_cmake.count("lfo_core") >= 3  # PUBLIC + test + bench (gh-174)
    cfg_cmake = (proj / "native/src/config/CMakeLists.txt").read_text("utf-8")
    assert "cjson_core" in cfg_cmake  # component extra_link_libs
    resamp_ext = (proj / "native/src/dsp/dsp_ext_resamp.c").read_text("utf-8")
    assert "Py_BEGIN_ALLOW_THREADS" in resamp_ext  # nogil

    # build + C tests -------------------------------------------------------
    build = proj / "build"
    cmake_args = [
        "cmake",
        "-S",
        str(proj),
        "-B",
        str(build),
        "-DBUILD_PYTHON=ON",
        *_cmake_gen(),
    ]
    if doppler_prefix:
        cmake_args.append(f"-DDoppler_DIR={_doppler_dir(doppler_prefix)}")
    _cmd(cmake_args, cwd=proj)
    _cmd(["cmake", "--build", str(build), "-j"], cwd=proj)
    _cmd(["ctest", "--test-dir", str(build), "--output-on-failure"], cwd=proj)

    # Python smoke test -----------------------------------------------------
    env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    if doppler_prefix:
        env["KITCHEN_SINK_DOPPLER"] = "1"
    _cmd([sys.executable, str(HERE / "smoke.py")], cwd=proj, env=env)
