"""gh-2843 item 1: a collocated object keeps what its module's core held.

``jm object m --module m`` creates the object named for its module, whose
core IS the module's own ``m_core.h`` / ``m_core.c``. The verb rendered both
over whatever the module had put there:

- the header lost the declarations ``jm function`` had injected, so
  ``m_ext.c`` called a function nothing declared, and ``status --check``
  reported the header STALE on a tree the verb had just written;
- ``_core.c`` lost the author's own functions, with no prompt.

Now the header is the object's render plus everything the module's held
(declarations, inline definitions, includes), and ``_core.c`` is only ever
appended to. Code jm cannot place is refused, with the tree unchanged.
The gate builds each tree and runs its C tests.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc
from _cmake import PYTHON3_ARG
from _jmrun import run_cli


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _module(tmp_path: Path) -> Path:
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "filt", cwd=proj)
    return proj


def _core(proj: Path) -> "tuple[Path, Path]":
    return (
        proj / "native" / "inc" / "p" / "filt" / "filt_core.h",
        proj / "native" / "src" / "filt" / "filt_core.c",
    )


def _status_ok(proj: Path) -> None:
    s = run_cli("status", "--check", cwd=proj)
    assert s.returncode == 0, s.stdout + s.stderr


def _build_and_test(proj: Path) -> None:
    build = proj / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(build),
            PYTHON3_ARG,
        ],
        ["cmake", "--build", str(build), "-j", "4"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


@pytest.mark.parametrize("inline", [False, True], ids=["extern", "inline"])
def test_the_header_keeps_the_modules_functions(tmp_path, inline):
    # The natural order: the module's function first, then the object.
    proj = _module(tmp_path)
    fn = ["function", "gain", "--module", "filt", "--param", "x:double"]
    _ok(
        *fn,
        "--return-type",
        "double",
        *(["--inline"] if inline else []),
        cwd=proj,
    )
    core_h, _ = _core(proj)
    before = core_h.read_text(encoding="utf-8")
    assert "p_gain" in before  # the premise: the module declared it
    _ok("object", "filt", "--module", "filt", cwd=proj)
    after = core_h.read_text(encoding="utf-8")
    assert "p_filt_create" in after  # the object's render is there...
    want = "p_gain(double x)" + ("" if inline else ";")
    assert want in after, after  # ...and so is the module's function
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


_AUTHOR_C = "\nint p_author_gh2843(void) { return 2843; }\n"


def test_core_c_keeps_the_authors_code(tmp_path):
    proj = _module(tmp_path)
    _, core_c = _core(proj)
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_AUTHOR_C)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = core_c.read_text(encoding="utf-8")
    assert _AUTHOR_C.strip() in text, text
    # Every definition the object needs was appended, including the
    # one-line `void p_filt_steps(` signature the house-style reader skips.
    for name in ("p_filt_create", "p_filt_destroy", "p_filt_steps"):
        assert f"{name}(" in text, name
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def _edit_header(
    proj: Path, after_inc: str = "", before_decl: str = ""
) -> None:
    core_h, _ = _core(proj)
    text = core_h.read_text(encoding="utf-8")
    inc = '#include "p/clib_common.h"\n'
    assert text.count(inc) == 1
    text = text.replace(inc, inc + after_inc)
    if before_decl:
        decl = "double p_gain(double x);"
        assert text.count(decl) == 1
        text = text.replace(decl, before_decl + decl)
    core_h.write_text(text, encoding="utf-8")


def test_the_authors_header_items_are_carried(tmp_path):
    # Every item the module's header held is carried whole: a Doxygen
    # block stays above its prototype, an #include and a macro go after
    # the render's own includes.
    proj = _module(tmp_path)
    _ok(
        "function",
        "gain",
        "--module",
        "filt",
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=proj,
    )
    doc = "/** @brief Authored gain, gh-2843. */\n"
    _edit_header(proj, "#include <string.h>\n#define FILT_TAPS 8\n", doc)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = _core(proj)[0].read_text(encoding="utf-8")
    assert doc + "double p_gain(double x);" in text, text
    assert text.count("#include <string.h>") == 1, text
    assert "#define FILT_TAPS 8" in text, text
    # An #include both headers have (clib_common.h) is not written twice.
    includes = [ln for ln in text.splitlines() if ln.startswith("#include")]
    assert len(includes) == len(set(includes)), text
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_code_jm_cannot_place_is_refused(tmp_path):
    # A conditional block cannot move without what it guards.
    proj = _module(tmp_path)
    _edit_header(proj, "#ifdef FILT_FAST\n#define FILT_TAPS 4\n#endif\n")
    tree = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli("object", "filt", "--module", "filt", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "#ifdef FILT_FAST" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == tree


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_plain_collocated_object_still_builds(tmp_path):
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _status_ok(proj)
    _build_and_test(proj)


# The verbs that rebuild the collocated object: each deleted the module's
# whole `native/src/<cname>/` -- a module function's `<fn>.c` and the
# author's own C in the core with it -- on exit 0 (gh-2843). `apply` is
# the control: it never deleted either.
_REBUILDS = {
    "regenerate-force": (("regenerate", "filt", "--force"), ""),
    "regenerate": (("regenerate", "filt"), "y\n"),
    "add-force": (
        ("add", "--state", "bias:double:1.0", "--object", "filt", "-f"),
        "",
    ),
    "remove-state": (
        ("remove", "state", "gain", "--object", "filt", "-f"),
        "",
    ),
    "apply": (("apply",), ""),
}
_GAIN_BODY = "return 2.0 * x; /* authored gain body, gh-2843 */"
_GAIN_DOC = "/**\n * @brief Authored gain doc, gh-2843.\n */\n"
_EXTRA = "\nint p_extra(void) { /* authored, gh-2843 */ return 7; }\n"


@pytest.mark.parametrize("verb", sorted(_REBUILDS))
def test_a_rebuild_keeps_every_authored_body(tmp_path, verb):
    proj = _module(tmp_path)
    _ok(
        "function",
        "gain",
        "--module",
        "filt",
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=proj,
    )
    _ok("object", "filt", "--module", "filt", cwd=proj)
    gain_c = proj / "native" / "src" / "filt" / "gain.c"
    text = gain_c.read_text(encoding="utf-8")
    stub = re.search(r"return [^;]*;", text)
    assert stub, text
    gain_c.write_text(
        text.replace(stub.group(0), _GAIN_BODY, 1), encoding="utf-8"
    )
    core_h, core_c = _core(proj)
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_EXTRA)
    # The author documents the module's function: the Doxygen above its
    # declaration is what its `.pyi` docstring is read from.
    text = core_h.read_text(encoding="utf-8")
    decl = "double p_gain(double x);"
    assert text.count(decl) == 1
    text = re.sub(
        r"/\*\*(?:(?!\*/).)*\*/\s*" + re.escape(decl),
        decl,
        text,
        count=1,
        flags=re.S,
    )
    core_h.write_text(text.replace(decl, _GAIN_DOC + decl), encoding="utf-8")
    _ok("apply", cwd=proj)
    pyi = proj / "src" / "p" / "filt" / "filt.pyi"
    assert "Authored gain doc, gh-2843." in pyi.read_text(encoding="utf-8")
    argv, stdin = _REBUILDS[verb]
    r = run_cli(*argv, cwd=proj, stdin=stdin)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _GAIN_BODY in gain_c.read_text(encoding="utf-8")
    assert _EXTRA.strip() in core_c.read_text(encoding="utf-8")
    assert _GAIN_DOC + decl in core_h.read_text(encoding="utf-8")
    # ...and the `.pyi` derived after the rebuild still reads it.
    assert "Authored gain doc, gh-2843." in pyi.read_text(encoding="utf-8")
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_re_added_object_keeps_its_kept_core(tmp_path):
    # `jm remove object` keeps the collocated object's core (gh-2141);
    # adding the object back keeps it too, and defines nothing twice.
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _, core_c = _core(proj)
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_EXTRA)
    _ok("remove", "object", "filt", "--force", cwd=proj)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = core_c.read_text(encoding="utf-8")
    assert _EXTRA.strip() in text, text
    assert len(re.findall(r"^p_filt_create\(", text, re.M)) == 1, text
    includes = [ln for ln in text.splitlines() if ln.startswith("#include")]
    assert len(includes) == len(set(includes)), text
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_re_add_of_another_shape_is_refused(tmp_path):
    # The kept core defines the previous object's create(); a re-add whose
    # create() takes other parameters would not build, so it is refused,
    # naming it, with the tree as it was.
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _ok("remove", "object", "filt", "--force", cwd=proj)
    tree = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli(
        "object", "filt", "--module", "filt", "--init-param", "k:int", cwd=proj
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "already defines p_filt_create" in r.stderr, r.stderr
    assert "Delete (or update)" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == tree


@pytest.mark.parametrize("in_core", [False, True], ids=["own-file", "in-core"])
def test_a_module_function_named_like_the_object_is_kept(tmp_path, in_core):
    # `filt_norm` in module `filt` is `p_filt_norm` in C: the object's
    # prefix, but the module's function. Its body in the shared `_core.c`
    # (--functions-in-core) is not the previous object's code, so adding
    # the object is not refused over it, and a rebuild keeps it.
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok(
        "module",
        "filt",
        *(["--functions-in-core"] if in_core else []),
        cwd=proj,
    )
    _ok(
        "function",
        "filt_norm",
        "--module",
        "filt",
        "--param",
        "x:double",
        "--return-type",
        "double",
        cwd=proj,
    )
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _ok("regenerate", "filt", "--force", cwd=proj)
    core_h, _ = _core(proj)
    assert "double p_filt_norm(double x);" in core_h.read_text(
        encoding="utf-8"
    )
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_core_c_without_the_header_include_gets_it(tmp_path):
    # The author's own `_core.c`, with no #include of the core header: the
    # object's definitions need the header's types, so it gets the render's.
    proj = _module(tmp_path)
    _, core_c = _core(proj)
    core_c.write_text(_EXTRA.lstrip(), encoding="utf-8")
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = core_c.read_text(encoding="utf-8")
    assert '#include "p/filt/filt_core.h"' in text, text
    assert _EXTRA.strip() in text, text
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_re_add_leaving_stale_accessors_is_refused(tmp_path):
    # Same create() (its params are the init params), another state: the
    # kept core's accessors read a field the new struct has not.
    proj = _module(tmp_path)
    _ok(
        "object",
        "filt",
        "--module",
        "filt",
        "--init-param",
        "k:int",
        "--state",
        "a:double:1.0",
        cwd=proj,
    )
    _ok("remove", "object", "filt", "--force", cwd=proj)
    tree = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli(
        "object",
        "filt",
        "--module",
        "filt",
        "--init-param",
        "k:int",
        "--state",
        "b:double:1.0",
        cwd=proj,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "p_filt_get_a" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == tree


@pytest.mark.parametrize(
    "module_flag, fn_flag, where",
    [("--functions-in-core", "", "c"), ("", "--inline", "h")],
    ids=["functions-in-core", "inline"],
)
def test_a_discarding_rebuild_keeps_a_module_functions_body(
    tmp_path, module_flag, fn_flag, where
):
    # A module function's body in the shared core (gh-247's
    # --functions-in-core, or --inline in the header) is the author's: the
    # render holds a stub for it, and --discard discards the OBJECT's
    # bodies, not the module's.
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "filt", *([module_flag] if module_flag else []), cwd=proj)
    _ok(
        "function",
        "gain",
        "--module",
        "filt",
        "--param",
        "x:double",
        "--return-type",
        "double",
        *([fn_flag] if fn_flag else []),
        cwd=proj,
    )
    _ok("object", "filt", "--module", "filt", cwd=proj)
    core_h, core_c = _core(proj)
    f = core_c if where == "c" else core_h
    text = f.read_text(encoding="utf-8")
    m = re.search(r"p_gain\(double x\)\s*\{(.*?)\n\}", text, re.S)
    assert m, text
    body = "\n    return 3.0 * x; /* authored, gh-2843 */"
    f.write_text(
        text[: m.start(1)] + body + text[m.end(1) :], encoding="utf-8"
    )
    _ok(
        "add", "--state", "bias:double:1.0", "--object", "filt", "-f", cwd=proj
    )
    assert body.strip() in f.read_text(encoding="utf-8")
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_macro_and_a_typedef_are_carried_on_their_own(tmp_path):
    # With no #include beside them: the items that go after the render's
    # includes (a macro, a typedef) are carried by themselves.
    proj = _module(tmp_path)
    _edit_header(proj, "#define FILT_TAPS 8\ntypedef int filt_tap_t;\n")
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = _core(proj)[0].read_text(encoding="utf-8")
    assert "#define FILT_TAPS 8" in text, text
    assert "typedef int filt_tap_t;" in text, text
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_reformatted_kept_definition_is_not_a_new_signature(tmp_path):
    # LLVM and Google styles join a return type to the name: the same
    # create() on one line is not "another signature".
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _, core_c = _core(proj)
    text = core_c.read_text(encoding="utf-8")
    two = "p_filt_state_t *\np_filt_create(double gain)"
    assert text.count(two) == 1
    core_c.write_text(
        text.replace(two, "p_filt_state_t *p_filt_create(double  gain)"),
        encoding="utf-8",
    )
    _ok("remove", "object", "filt", "--force", cwd=proj)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


_SSE = "\n#ifdef __SSE2__\nint p_g_sse(void) { return 1; }\n#endif\n"


def test_a_rebuild_refuses_to_drop_a_conditional_block(tmp_path):
    # A standalone object: `regenerate` restores the bodies it can name,
    # and a preprocessor block guarding definitions is not one of them.
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("object", "g", cwd=proj)
    core_c = proj / "native" / "src" / "g" / "g_core.c"
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_SSE)
    before = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli("regenerate", "g", "--force", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "#ifdef __SSE2__" in r.stderr, r.stderr
    assert "--discard" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == before
    # --discard says it discards what the author wrote, and does.
    _ok("regenerate", "g", "--discard", "--force", cwd=proj)
    assert "__SSE2__" not in core_c.read_text(encoding="utf-8")


def _kept_create(tmp_path: Path, old: str, new: str) -> Path:
    """A collocated object removed with its core kept, the kept
    definition of create() edited from *old* to *new* by the author."""
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _, core_c = _core(proj)
    text = core_c.read_text(encoding="utf-8")
    assert text.count(old) == 1, text
    core_c.write_text(text.replace(old, new), encoding="utf-8")
    _ok("remove", "object", "filt", "--force", cwd=proj)
    return proj


def test_a_renamed_parameter_is_not_a_new_signature(tmp_path):
    # The author renamed create()'s parameter, body and all: the compiler
    # sees the same signature, so the re-add keeps it.
    proj = _kept_create(
        tmp_path,
        "p_filt_create(double gain)\n{",
        "p_filt_create(double g)\n{",
    )
    _, core_c = _core(proj)
    core_c.write_text(
        core_c.read_text(encoding="utf-8").replace(
            "obj->gain = gain;", "obj->gain = g;"
        ),
        encoding="utf-8",
    )
    _ok("object", "filt", "--module", "filt", cwd=proj)
    assert "p_filt_create(double g)" in core_c.read_text(encoding="utf-8")
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_a_changed_parameter_type_is_refused(tmp_path):
    proj = _kept_create(
        tmp_path,
        "p_filt_create(double gain)\n{",
        "p_filt_create(float gain)\n{",
    )
    r = run_cli("object", "filt", "--module", "filt", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "already defines p_filt_create" in r.stderr, r.stderr


def test_discard_does_not_drop_the_modules_conditional_block(tmp_path):
    # The collocated object's core is its module's too: a block in it is
    # module code, which --discard (the object's bodies only) cannot drop.
    # A standalone object's core is its own, and --discard drops it there.
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _, core_c = _core(proj)
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_SSE.replace("p_g_sse", "p_filt_sse"))
    before = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli("regenerate", "filt", "--discard", "--force", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    said = " ".join(r.stderr.split())
    assert "the block is module code, which --discard does not drop" in said
    assert "--discard discards only the object's own bodies" in said
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == before
