"""gh-2259 part 2: a generating source delegates named generator methods.

A composer's generating source (``[module.X.source.generates]``) bound
exactly ``steps`` / ``step`` / ``reset`` of its generator, so anything else
the generator declared stayed on the generator. doppler's public
``wfm.Synth`` is such a source, and its generator ``wfm_synth`` declares
``prod`` -- a ``batch`` method -- which ``Synth`` could not reach.

``[module.X.source.generates] methods = ["prod", ...]`` names the ones the
source exposes. Each is rendered by the emitter that binds it on the
generator (`make_methods_ctx`), with a `MethodHost` moving only the C it is
built around: ``self`` is the source, the state is the generator ``steps``
builds through ``bridge_fn`` and caches, and the first call builds it with
``steps``'s refusal. Its runtime doc and ``.pyi`` member are the
generator's. A name is refused at load when the generator does not declare
it, when the source already has the member, or when the source cannot carry
the method's binding.

GATE: built and driven, ``Src(...).prod(x)`` builds the generator on its
first call and returns the kernel's result, and continues the generator
``steps`` uses (and the reverse); a refused build raises the bridge's
``ValueError`` / ``RuntimeError``; every delegated wrapper is rebound (no
``self->handle``, no generator object type) and compiles under ``-Werror``;
the runtime doc and ``.pyi`` member equal the generator's; each refusal
stops ``load``; the key survives ``apply`` in a split layout.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _keys
from just_makeit import _config as C

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "dlg"

#: One method per wrapper shape the emitter renders differently: a scalar,
#: both `batch` forms, a variable-output method with and without an input
#: (the second sizing its output from `state`, the alias a host must
#: rebind), and a value-or-negative-error int. `prod` and `gain` are driven;
#: the rest are held to compiling, warning-clean, with nothing left bound to
#: the generator's own type.
SHAPES = {
    "gain": ["--arg-type", "double", "--return-type", "double"],
    "prod": [
        "--arg-type",
        "float _Complex",
        "--return-type",
        "float _Complex",
        "--batch",
    ],
    "fill": [
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
        "--batch",
    ],
    "pull": [
        "--arg-type",
        "float _Complex",
        "--return-type",
        "float _Complex",
        "--variable-output",
    ],
    "snap": [
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
        "--variable-output",
        "--count-default",
        "(size_t)state->phase + 1",
    ],
    "pick": [
        "--arg-type",
        "double",
        "--return-type",
        "int",
        "--error-negative",
    ],
}

#: The authored brief `gain` carries, so doc parity compares a document.
GAIN_BRIEF = "Offset x by the generator's current phase."

MIX_H = """#ifndef MIX_CORE_H
#define MIX_CORE_H
#include <complex.h>
#include <stddef.h>
typedef struct { double start; } src_t;
typedef struct {
  src_t *sources;
  size_t n_sources;
  size_t dur;
  double fs;
} seg_t;
typedef struct mix_state mix_state_t;
mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);
size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);
const seg_t *mix_segments(const mix_state_t *st, size_t *n, int *r, int *c);
void mix_destroy(mix_state_t *st);
#endif
"""

MIX_C = f"""#include "{PKG}/mix/mix_core.h"
#include <stdlib.h>
struct mix_state {{ int unused; }};
mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c)
{{ (void)s; (void)n; (void)r; (void)c; return calloc(1, sizeof(mix_state_t)); }}
size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m)
{{ (void)st; (void)o; (void)m; return 0; }}
const seg_t *mix_segments(const mix_state_t *st, size_t *n, int *r, int *c)
{{ (void)st; *n = 0; *r = 0; *c = 0; return NULL; }}
void mix_destroy(mix_state_t *st) {{ free(st); }}
"""

#: The bridge refuses a negative start: -1 with a reason, -2 without one.
BRIDGE_C = f"""#include "{PKG}/mix/mix_bridge.h"
{PKG}_gen_state_t *src_to_gen(const src_t *s, double fs)
{{
  (void)fs;
  if (s->start < 0.0)
    return NULL;
  return {PKG}_gen_create(s->start);
}}
const char *src_why_not(const src_t *s, double fs)
{{
  (void)fs;
  return s->start == -1.0 ? "start must be >= 0" : NULL;
}}
"""

BACKING_CMAKE = """\
add_library(backing_core OBJECT mix_core.c mix_bridge.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
"""


def _composer_table(methods: "list[str]") -> dict:
    """``[module.mix]``: a composer whose source generates through `gen`."""
    return {
        "kind": "composer",
        "backing": "mix",
        "composes": ["gen"],
        "depends_on": [{"name": "gen", "link": True}],
        "extra_link_libs": ["backing_core"],
        "source": {
            "object": "gen",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [{"name": "start", "type": "double", "default": "0.0"}],
            "computed": [],
            "generates": {
                "generator": "gen",
                "bridge_fn": "src_to_gen",
                "bridge_error_fn": "src_why_not",
                "methods": list(methods),
            },
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [{"name": "dur", "type": "size_t", "default": "4"}],
        },
        "oo": {"composer_type_name": "Mix"},
    }


def _ok(r) -> None:
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]


def _scaffold(
    tmp_path: Path,
    *,
    module: bool = True,
    methods: "list[str] | None" = None,
    shapes: "list[str] | None" = None,
) -> Path:
    """A project whose composer source delegates *methods* of `gen`.

    `gen` is a mutable void-input generator: each sample is its phase, and
    the phase advances by one per sample, so which generator a call ran
    against is readable off the numbers. It lives in module `sig` (as
    doppler's `wfm_synth` lives in `wfm`) unless *module* is false.
    """
    shapes = list(SHAPES) if shapes is None else shapes
    methods = shapes if methods is None else methods
    _ok(run_cli("new", PKG, cwd=tmp_path))
    proj = tmp_path / PKG
    where: "list[str]" = []
    if module:
        _ok(run_cli("module", "sig", cwd=proj))
        where = ["--module", "sig"]
    _ok(
        run_cli(
            "object",
            "gen",
            *where,
            "--mutable",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--state",
            "phase:double:0.0",
            cwd=proj,
        )
    )
    for name in shapes:
        _ok(run_cli("method", "gen", name, *SHAPES[name], cwd=proj))

    # The kernels: step yields the phase and advances it; prod scales its
    # input by the next samples of that same generator; gain reads it.
    hdr = proj / "native" / "inc" / PKG / "gen" / "gen_core.h"
    text = hdr.read_text(encoding="utf-8")
    stub = (
        "    (void)state; /* TODO: implement */\n    return (float _Complex)0;"
    )
    brief = " * @brief gain.\n"
    assert stub in text and text.count(brief) == ("gain" in shapes), text
    text = text.replace(
        stub,
        "    float _Complex y = (float _Complex)state->phase;\n"
        "    state->phase += 1.0;\n"
        "    return y;",
    ).replace(brief, f" * @brief {GAIN_BRIEF}\n")
    hdr.write_text(text, encoding="utf-8")
    core = proj / "native" / "src" / "gen" / "gen_core.c"
    text = core.read_text(encoding="utf-8")
    kernels = {
        "prod": (
            "    (void)state; (void)in; (void)n; (void)out;\n",
            "    for (size_t i = 0; i < n; i++)\n"
            f"        out[i] = in[i] * {PKG}_gen_step(state);\n",
        ),
        "gain": (
            "    (void)state; (void)x;\n    return (double)0.0;\n",
            "    return x + state->phase;\n",
        ),
    }
    for name, (old, new) in kernels.items():
        if name in shapes:
            assert text.count(old) == 1, (old, text)
            text = text.replace(old, new)
    core.write_text(text, encoding="utf-8")

    inc = proj / "native" / "inc" / PKG / "mix"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mix_core.h").write_text(MIX_H, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "mix_core.c").write_text(MIX_C, encoding="utf-8")
    (backing / "mix_bridge.c").write_text(BRIDGE_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {})["mix"] = _composer_table(methods)
    C.save(proj, cfg)
    return proj


def _member(pyi: Path, cls: str, name: str) -> "str | None":
    """The text of ``cls.name`` in the stub *pyi*, or None when absent."""
    text = pyi.read_text(encoding="utf-8")
    for node in ast.parse(text).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(text, item)
    return None


def _gen_pyi(proj: Path, module: bool) -> Path:
    if module:
        return proj / "src" / PKG / "sig" / "sig.pyi"
    return proj / "src" / PKG / "gen.pyi"


# ── the render: every wrapper rebound onto the source ────────────────────


@pytest.fixture(scope="module")
def applied(tmp_path_factory) -> Path:
    """The module-generator project, applied (no build)."""
    proj = _scaffold(tmp_path_factory.mktemp("gh2259"))
    _ok(run_cli("apply", cwd=proj))
    return proj


def _ext(proj: Path) -> str:
    return (proj / "native" / "src" / "mix" / "mix_ext.c").read_text(
        encoding="utf-8"
    )


def test_every_delegated_wrapper_is_bound_to_the_source(applied):
    """No wrapper is left reading the generator object's own struct."""
    ext = _ext(applied)
    assert "self->handle" not in ext
    assert "GenObject" not in ext
    for name in SHAPES:
        start = ext.index(f"\nSrc_gen_{name}(SrcObject *self")
        body = ext[start : ext.index("\n}\n", start)]
        # Built on the first call, with `steps`'s refusal.
        assert "    if (Src_ensure_gen(self) < 0)\n        return NULL;" in (
            body
        ), body
        assert f'{{"{name}", ' in ext and f"Src_gen_{name}," in ext


def test_the_runtime_doc_is_the_generators(applied):
    """The row's doc text, byte for byte, as the generator's row has it."""
    gen_c = "".join(
        p.read_text(encoding="utf-8")
        for p in (applied / "native" / "src" / "sig").glob("*.c")
    )
    ext = _ext(applied)

    def doc(text: str, fn: str) -> str:
        at = text.index(f"{fn},")
        return text[at : text.index("},\n", at)].split("\n", 1)[1]

    assert GAIN_BRIEF in doc(ext, "Src_gen_gain")
    for name in SHAPES:
        assert doc(ext, f"Src_gen_{name}") == doc(gen_c, f"Gen_{name}"), name


@pytest.mark.parametrize("module", [True, False], ids=["module", "standalone"])
def test_the_pyi_member_is_the_generators(tmp_path, module):
    """Each delegated member, as the generator's own stub writes it.

    A `batch` method's `.pyi` member is gh-2259 part 1's: the emitter
    writes none yet, while the module stub writes a scalar-typed one. It
    reaches the source through that emitter, so once written it is held to
    the generator's like every other member.
    """
    proj = _scaffold(tmp_path, module=module)
    _ok(run_cli("apply", cwd=proj))
    mix = proj / "src" / PKG / "mix" / "mix.pyi"
    gen = _gen_pyi(proj, module)
    assert GAIN_BRIEF in (_member(mix, "Src", "gain") or "")
    for name, flags in SHAPES.items():
        ours = _member(mix, "Src", name)
        if "--batch" in flags and ours is None:
            continue
        assert ours is not None, name
        assert ours == _member(gen, "Gen", name), name


# ── the refusals, at load ─────────────────────────────────────────────────


def _base() -> dict:
    """A manifest dict: generator `gen` with one method per kind refused."""
    return {
        "project": {"name": PKG},
        "gen": {
            "arg_type": "void",
            "return_type": "float _Complex",
            "state": [{"name": "phase", "type": "double", "default": "0"}],
            "methods": [
                {"name": "prod", "batch": True},
                {"name": "va", "varargs": True},
                {"name": "hand", "manual_stub": True},
                {"name": "wait", "borrow": True},
                {"name": "done", "releases": ["wait"]},
                {
                    "name": "peak",
                    "single": True,
                    "result_fields": [{"name": "i", "type": "int"}],
                },
                {"name": "get_phase"},
            ],
        },
        "module": {"mix": _composer_table(["prod"])},
    }


REFUSED = {
    "undeclared": ("nope", "declares no method 'nope'"),
    "field": ("start", "already the source's field"),
    "fs": ("fs", "already the source's field"),
    "steps": ("steps", "already the source's generation method"),
    "step": ("step", "already the source's generation method"),
    "reset": ("reset", "already the source's generation method"),
    "computed": ("span", "already the source's computed property"),
    "varargs": ("va", "binding is hand-written"),
    "manual_stub": ("hand", "binding is hand-written"),
    "borrow": ("wait", "lends memory the generator owns"),
    "release": ("done", "lends memory the generator owns"),
    "single": ("peak", "returns a record type"),
    "builtin": ("get_phase", "built-in members"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_each_refusal_names_the_entry(case):
    name, why = REFUSED[case]
    cfg = _base()
    src = cfg["module"]["mix"]["source"]
    src["computed"] = [{"name": "span", "type": "double", "fn": "f"}]
    src["generates"]["methods"] = ["prod", name]
    errors = _composer.delegated_method_errors(cfg)
    assert len(errors) == 1, errors
    assert repr(name) in errors[0] and why in errors[0], errors[0]


def test_a_name_listed_twice_and_a_non_list_are_refused():
    cfg = _base()
    cfg["module"]["mix"]["source"]["generates"]["methods"] = ["prod", "prod"]
    assert "listed twice" in _composer.delegated_method_errors(cfg)[0]
    cfg["module"]["mix"]["source"]["generates"]["methods"] = "prod"
    assert "must be an array" in _composer.delegated_method_errors(cfg)[0]


def test_the_base_delegation_is_accepted():
    """The refusals above are each the ONE error: the rest is accepted."""
    assert _composer.delegated_method_errors(_base()) == []


def test_load_refuses_before_anything_is_written(tmp_path):
    """Refused by `load`, which every command passes through."""
    proj = _scaffold(tmp_path, methods=["prod", "nope"], shapes=["prod"])
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0
    assert "declares no method 'nope'" in r.stderr, r.stderr
    assert not (proj / "native" / "src" / "mix").exists()


def test_nothing_is_delegated_unless_named(tmp_path):
    """An empty list is the old surface: steps / step / reset only."""
    proj = _scaffold(tmp_path, methods=[], shapes=["prod"])
    _ok(run_cli("apply", cwd=proj))
    ext = _ext(proj)
    assert "Src_gen_" not in ext and '{"prod"' not in ext
    assert _member(proj / "src" / PKG / "mix" / "mix.pyi", "Src", "prod") is (
        None
    )


def test_the_key_survives_apply_in_both_layouts(tmp_path):
    """Split (the default) keeps it in the module's fragment; a single-file
    manifest reads it inline, and `migrate-to-fragments` carries it into the
    fragment it writes."""
    proj = _scaffold(tmp_path, methods=["prod"], shapes=["prod"])
    frag = proj / "modules" / "mix.toml"
    manifest = proj / "just-makeit.toml"

    def delegated() -> None:
        _ok(run_cli("apply", cwd=proj))
        cfg = C.load(proj)
        gen = cfg["module"]["mix"]["source"]["generates"]
        assert gen["methods"] == ["prod"]
        assert "Src_gen_prod" in _ext(proj)
        # A key jm reads is one it recognises (gh-816).
        assert _keys.unknown_keys(cfg) == []

    delegated()
    assert 'methods = ["prod"]' in frag.read_text(encoding="utf-8")
    # The same project as ONE file: every fragment folded in, no `include`.
    single = C._dump(C.load(proj))
    shutil.rmtree(proj / "objects")
    shutil.rmtree(proj / "modules")
    manifest.write_text(single, encoding="utf-8")
    assert 'methods = ["prod"]' in single
    delegated()
    _ok(run_cli("migrate-to-fragments", cwd=proj))
    assert 'methods = ["prod"]' in frag.read_text(encoding="utf-8")
    assert "[module.mix" not in manifest.read_text(encoding="utf-8")
    delegated()


# ── built and driven ──────────────────────────────────────────────────────

#: Run in the built project. Prints a JSON list of problems, each a
#: sentence; empty is a pass.
_DRIVER = r"""
import json, sys
sys.path.insert(0, "src")
import numpy as np
from dlg.mix.mix import Src
from dlg.sig import Gen

problems = []


def check(cond, what):
    if not cond:
        problems.append(what)


one = np.ones(3, dtype=np.complex64)

# prod builds the generator on its first call, from this configuration.
s = Src(start=10.0)
got = s.prod(one)
check(np.array_equal(got, [10, 11, 12]), f"first prod gave {got}")
check(np.array_equal(one, [1, 1, 1]), "prod wrote into its input")
# ...and steps continues the generator prod advanced, and the reverse.
got = s.steps(2)
check(np.array_equal(got, [13, 14]), f"steps after prod gave {got}")
got = s.prod(one[:1])
check(np.array_equal(got, [15]), f"prod after steps gave {got}")
check(s.gain(1.0) == 17.0, f"gain read phase {s.gain(1.0) - 1}")
t = Src()
check(np.array_equal(t.steps(2), [0, 1]), "a fresh source did not start at 0")
check(np.array_equal(t.prod(one[:2]), [2, 3]), "prod did not continue steps")
# out= is the generator's binding too.
buf = np.zeros(2, dtype=np.complex64)
r = Src(start=5.0).prod(one[:2], out=buf)
check(r is buf and np.array_equal(buf, [5, 6]), f"out= gave {buf}")

# A refused build is steps's refusal.
try:
    Src(start=-1.0).prod(one)
    problems.append("a refused build did not raise")
except ValueError as e:
    check(str(e) == "start must be >= 0", f"ValueError said {e}")
try:
    Src(start=-2.0).gain(1.0)
    problems.append("a reasonless refusal did not raise")
except RuntimeError as e:
    check("src_to_gen returned NULL" in str(e), f"RuntimeError said {e}")

# The generator's own document, on both faces of the delegated member.
for name in ("prod", "gain"):
    check(
        getattr(Src, name).__doc__ == getattr(Gen, name).__doc__,
        f"Src.{name}.__doc__ is not Gen.{name}.__doc__",
    )
print(json.dumps(problems))
"""


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_source_runs_its_generators_methods(tmp_path):
    proj = _scaffold(tmp_path)
    _ok(run_cli("apply", cwd=proj))

    def run(cmd: list) -> None:
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])

    # Warning-clean: every delegated shape is new code in a file `apply`
    # regenerates, and a downstream builds it with -Werror.
    run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ]
    )
    run(["cmake", "--build", "build", "--parallel", "4"])
    driver = proj / "drive_gh2259.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    problems = json.loads(r.stdout)
    assert not problems, "\n".join(problems)
