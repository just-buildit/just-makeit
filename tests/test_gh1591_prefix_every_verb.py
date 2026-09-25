"""gh-1591 2b: every jm verb works on a project with the DEFAULT prefix.

`jm new` now prefixes every derived C symbol with the package name, so
every verb meets a prefixed tree. The phase-1b oracles prove what jm
RENDERS carries the stem; they cannot see a verb that SEARCHES a real tree
for a bare name, or looks something up by one, and quietly does nothing
when it is not there. Making the prefix the default found eight: each is a
test here, on a project exactly as `jm new` makes it.

GATE: on a default-prefixed project, `jm perf` retrofits step(), a field
      property lands in the state struct, the gh-1076 constructor check can
      read the header, a list-of-records method's prototype takes the
      prefixed state, `jm app --function` calls the prefixed symbol, a
      module function's Doxygen reaches its stub, and `jm bind` accepts the
      project's own header.
"""

from __future__ import annotations

from pathlib import Path

from _jminc import INC_ROOT
from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _ctorsig


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _proj(tmp_path: Path, *new_args: str) -> Path:
    _ok("new", "p", *new_args, cwd=tmp_path)
    root = tmp_path / "p"
    assert C.c_prefix(C.load(root)) == "p", "jm new no longer defaults"
    return root


def _header(root: Path, comp: str) -> str:
    return (root / INC_ROOT / comp / f"{comp}_core.h").read_text("utf-8")


def test_perf_retrofits_the_prefixed_step(tmp_path):
    root = _proj(tmp_path, "--object", "g")
    _ok("perf", cwd=root)
    assert "JM_FORCEINLINE JM_HOT" in _header(root, "g")


def test_a_field_property_lands_in_the_prefixed_struct(tmp_path):
    root = _proj(tmp_path, "--object", "g")
    _ok("property", "g", "rate", "--type", "double", "--field", cwd=root)
    h = _header(root, "g")
    assert "double rate;" in h[: h.index("} p_g_state_t;")], h


def test_the_ctor_check_reads_the_prefixed_header(tmp_path):
    """gh-1076's check returns None -- 'nothing to compare' -- when it cannot
    find the declaration, so a blind check reads as a clean one."""
    root = _proj(tmp_path, "--object", "g", "--state", "gain:double:1.0")
    assert _ctorsig.declared_params(root, "g", "p_g_create") is not None


def test_the_code_example_check_reads_the_prefixed_call(tmp_path):
    """gh-1502's advisory finds the @code example's create() call by the
    `<stem>_state_t *` it assigns to; the bare spelling found no call, so a
    mis-counted example was never reported."""
    root = _proj(tmp_path)
    _ok("object", "g", "--init-param", "n:int:4", cwd=root)
    h_path = root / INC_ROOT / "g" / "g_core.h"
    h = h_path.read_text("utf-8")
    call = "p_g_create(n)"
    assert call in h, h
    h_path.write_text(h.replace(call, "p_g_create(n, 5)", 1), "utf-8")
    found = _ctorsig.example_drift(root, C.load(root))
    assert [(d.component, d.passed, d.declared) for d in found] == [
        ("g", 2, 1)
    ], found


def test_a_record_list_method_takes_the_prefixed_state(tmp_path):
    root = _proj(tmp_path, "--object", "g")
    _ok(
        "method",
        "g",
        "peaks",
        "--arg-type",
        "float[]",
        "--return-type",
        "pk_t",
        "--result-field",
        "i:int",
        cwd=root,
    )
    # The CLI writes the declaration through `_method`'s prototype; its
    # manifest peer is pinned by the next test.
    decl = next(
        ln for ln in _header(root, "g").splitlines() if "p_g_peaks(" in ln
    )
    assert "p_g_state_t *state" in decl, decl


def test_the_manifest_record_list_prototype_takes_the_prefixed_state():
    """`make_methods_ctx`'s declaration chain -- the one a header rendered
    whole from the manifest reads (`method_decls`) -- spelled the state type
    with `.format(component)`, which the f-string ratchet could not see."""
    from just_makeit import _context as Ctx

    ctx = Ctx.make_methods_ctx(
        "g",
        "G",
        [
            {
                "name": "peaks",
                "arg_type": "float[]",
                "return_type": "pk_t",
                "result_fields": [{"name": "i", "type": "int"}],
            }
        ],
        csym="p_g",
    )
    decl = next(
        ln for ln in ctx["method_decls"].splitlines() if "p_g_peaks(" in ln
    )
    assert "p_g_state_t *state" in decl, decl


def test_app_function_calls_the_prefixed_symbol(tmp_path):
    root = _proj(tmp_path)
    _ok("module", "m", cwd=root)
    _ok(
        "function",
        "addn",
        "--module",
        "m",
        "--param",
        "a:float",
        "--param",
        "b:float",
        "--return-type",
        "float",
        cwd=root,
    )
    _ok("app", "--target", "c", "--function", "addn", "--name", "t", cwd=root)
    app = (root / "native" / "src" / "app" / "t.c").read_text("utf-8")
    assert "p_addn(a, b)" in app, app


def test_a_module_functions_doxygen_reaches_its_stub(tmp_path):
    root = _proj(tmp_path)
    _ok("module", "m", cwd=root)
    _ok(
        "function",
        "lin",
        "--module",
        "m",
        "--param",
        "x:float",
        "--return-type",
        "float",
        cwd=root,
    )
    h_path = root / INC_ROOT / "m" / "m_core.h"
    h = h_path.read_text("utf-8")
    decl = "float p_lin(float x);"
    assert decl in h, h
    h_path.write_text(
        h.replace(decl, "/** @brief Linear, documented. */\n" + decl, 1),
        "utf-8",
    )
    _ok("apply", cwd=root)
    pyi = (root / "src" / "p" / "m" / "m.pyi").read_text("utf-8")
    assert "Linear, documented." in pyi, pyi


def test_bind_accepts_the_projects_own_header(tmp_path):
    root = _proj(tmp_path, "--object", "g", "--state", "gain:double:1.0")
    _ok("bind", "g", "--check", cwd=root)
