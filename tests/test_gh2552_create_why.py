"""gh-2552: an object's constructor can name why it refused.

``create_error`` / ``create_error_message`` (gh-482) turn a NULL
``create()`` into the declared exception, but with ONE message for every
refusal. doppler's single ``Correlator`` refuses about twenty knob
combinations; with one message the caller cannot tell which knob to change.

``create_why = true`` is gh-1706's ``why`` on the constructor, and the
composer's ``create_why`` (gh-1755) on an object and a view: the C
constructor takes a trailing ``const char **why`` after every init param,
the binding passes the address of a local initialised to NULL, and a NULL
return raises ``create_error`` with the sentence written there, or
``create_error_message`` when nothing was. The parameter never reaches
Python. Without a ``create_error`` nothing would raise the sentence, so the
key is refused, as ``why`` without ``check_return`` is.

The project is built by running jm -- every object here is declared in the
manifest and materialised by ``jm apply``, so the header, the ``_core.c``
stub, the binding, the C test and the bench are all jm's render -- and only
the constructor BODIES are written by hand, as an author would.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _ctorsig
from just_makeit._context import make_errors_ctx, make_state_ctx

COR_MSG = "cor refused"
ACC_MSG = "acc refused"

#: The sentence each constructor writes on its ``mode == 1`` refusal.
SAID = {
    "rs_cor_create": "Cor: mode 1 is not supported",
    "rs_acc_create": "Acc: mode 1 is not supported",
    "rs_acc_create_burst": "AccBurst: mode 1 has no burst form",
    "rs_hdr_create": "Hdr: mode 1 is not supported",
}

#: Standalone ``cor`` (and a header-only ``hdr``) declare their own
#: translation; module object ``acc`` its own, which the view ``AccBurst``
#: inherits while stating its own ``create_why``.
COR_TOML = f"""
[cor]
arg_type = "float"
return_type = "float"
create_error = "ValueError"
create_error_message = "{COR_MSG}"
create_why = true

[[cor.init_params]]
name = "mode"
type = "int"
default = "0"
"""

HDR_TOML = f"""
[hdr]
arg_type = "float"
return_type = "float"
header_only = "true"
create_error = "ValueError"
create_error_message = "{COR_MSG}"
create_why = true

[[hdr.init_params]]
name = "mode"
type = "int"
default = "0"
"""

ACC_TOML = f"""
[acc]
arg_type = "float"
return_type = "float"
create_error = "RuntimeError"
create_error_message = "{ACC_MSG}"
create_why = true

[[acc.init_params]]
name = "mode"
type = "int"
default = "0"

[[acc.views]]
class_name = "AccBurst"
create_fn = "rs_acc_create_burst"
create_why = true

[[acc.views.init_params]]
name = "mode"
type = "int"
default = "0"
"""


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _project(root: Path) -> Path:
    """``rs`` with ``cor``, ``hdr`` and module ``dsp``'s ``acc`` +
    ``AccBurst``, every one declared in the manifest and written by
    ``jm apply``."""
    _ok("new", "rs", "--object", "seed", cwd=root)
    proj = root / "rs"
    _ok("module", "dsp", cwd=proj)
    (proj / "objects" / "cor.toml").write_text(COR_TOML, encoding="utf-8")
    (proj / "objects" / "hdr.toml").write_text(HDR_TOML, encoding="utf-8")
    (proj / "objects" / "acc.toml").write_text(ACC_TOML, encoding="utf-8")
    mod = proj / "modules" / "dsp.toml"
    text = mod.read_text(encoding="utf-8")
    assert text.count("objects = []") == 1, text
    mod.write_text(text.replace("objects = []", 'objects = ["acc"]'), "utf-8")
    _ok("apply", cwd=proj)
    return proj


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2552"))


# -- the scaffold jm writes ----------------------------------------------------


def test_the_scaffold_is_clean(scaffold: Path) -> None:
    r = run_cli("status", "--check", cwd=scaffold)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    "comp, fn",
    [("cor", "rs_cor_create"), ("acc", "rs_acc_create"),
     ("acc", "rs_acc_create_burst")],
)  # fmt: skip
def test_header_and_stub_take_why_last(
    scaffold: Path, comp: str, fn: str
) -> None:
    """The prototype jm injects and the stub it scaffolds are one
    signature, ending in the trailing ``const char **why``."""
    hdr = (scaffold / "native/inc/rs" / comp / f"{comp}_core.h").read_text(
        "utf-8"
    )
    core = (scaffold / "native/src" / comp / f"{comp}_core.c").read_text(
        "utf-8"
    )
    found = {
        (s.kind, _ctorsig._norm(s.params))
        for text in (hdr, core)
        for s in _ctorsig.signatures(text)
        if s.name == fn
    }
    want = _ctorsig._norm("int mode, const char **why")
    assert found == {("decl", want), ("def", want)}, found


def test_header_only_inline_create_takes_why(scaffold: Path) -> None:
    """``header_only``'s ``static inline`` create() is the third face of
    the same prototype."""
    hdr = (scaffold / "native/inc/rs/hdr/hdr_core.h").read_text("utf-8")
    assert re.search(
        r"static inline rs_hdr_state_t \*\s*rs_hdr_create\(int mode, "
        r"const char \*\*why\)\s*\{",
        hdr,
    ), hdr


def test_the_header_documents_why(scaffold: Path) -> None:
    hdr = (scaffold / "native/inc/rs/cor/cor_core.h").read_text("utf-8")
    assert "@param why  Set to a static reason on refusal" in hdr


def test_c_test_and_bench_pass_null(scaffold: Path) -> None:
    test_c = (scaffold / "native/tests/test_cor_core.c").read_text("utf-8")
    assert "rs_cor_create(0, NULL)" in test_c


@pytest.mark.parametrize("stub", ["src/rs/cor.pyi", "src/rs/dsp/dsp.pyi"])
def test_why_is_not_in_python(scaffold: Path, stub: str) -> None:
    """Neither class's ``__init__`` in the ``.pyi`` names ``why``."""
    tree = ast.parse((scaffold / stub).read_text("utf-8"))
    inits = [
        fn
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for fn in cls.body
        if isinstance(fn, ast.FunctionDef) and fn.name == "__init__"
    ]
    assert inits
    for fn in inits:
        names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        assert "why" not in names, ast.unparse(fn)
        assert "mode" in names, ast.unparse(fn)


def test_a_header_without_why_is_a_finding(scaffold: Path, tmp_path) -> None:
    """A module object's sacred header is not rewritten by apply; the
    gh-1076 check is what names a prototype that lacks the parameter."""
    proj = tmp_path / "rs"
    shutil.copytree(scaffold, proj)
    hdr = proj / "native/inc/rs/acc/acc_core.h"
    text = hdr.read_text("utf-8")
    decl = "rs_acc_state_t *rs_acc_create(int mode, const char **why);"
    assert text.count(decl) == 1, text
    hdr.write_text(
        text.replace(decl, "rs_acc_state_t *rs_acc_create(int mode);"),
        "utf-8",
    )
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "CTOR" in out and "rs_acc_create(int mode)" in out, out


def test_script_names_the_manifest_only_key(scaffold: Path) -> None:
    """No verb spells it, so a replayed script must say so, not drop it."""
    out = _ok("script", cwd=scaffold)
    assert "# NOTE: [cor] create_why = true has no CLI flag" in out, out
    assert "# NOTE: [[acc.views]] 'AccBurst' create_why = true" in out, out


# -- refused at apply ----------------------------------------------------------


def _refusal(tmp_path: Path, toml: str) -> str:
    _ok("new", "rq", "--object", "seed", cwd=tmp_path)
    proj = tmp_path / "rq"
    (proj / "objects" / "cor.toml").write_text(toml, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def test_create_why_without_create_error_is_refused(tmp_path) -> None:
    toml = COR_TOML.replace(
        f'create_error = "ValueError"\ncreate_error_message = "{COR_MSG}"\n',
        "",
    )
    assert "create_error" not in toml.replace("create_why", "")
    out = _refusal(tmp_path, toml)
    assert "[cor] create_why = true needs a create_error" in out, out


def test_a_view_without_create_error_is_refused(tmp_path) -> None:
    """A view inherits its parent's create_error; with none on either the
    view's create_why has nothing to raise the sentence as."""
    toml = (
        ACC_TOML.replace(
            f'create_error = "RuntimeError"\n'
            f'create_error_message = "{ACC_MSG}"\ncreate_why = true\n',
            "",
        )
        .replace("[acc]", "[cor]")
        .replace("[[acc.", "[[cor.")
    )
    assert "create_error" not in toml
    out = _refusal(tmp_path, toml)
    assert "[[cor.views]] 'AccBurst' create_why = true needs a" in out, out


def test_a_non_bool_create_why_is_refused(tmp_path) -> None:
    out = _refusal(
        tmp_path,
        COR_TOML.replace("create_why = true", 'create_why = "rs_cor_why"'),
    )
    assert "create_why = 'rs_cor_why' must be true or false" in out, out


def test_an_init_param_named_why_is_refused(tmp_path) -> None:
    out = _refusal(tmp_path, COR_TOML.replace('name = "mode"', 'name = "why"'))
    assert "an init param named `why`" in out, out


# -- one emitter, both faces ---------------------------------------------------


def test_without_the_key_the_render_is_unchanged() -> None:
    """``create_why`` absent and ``false`` render alike, byte for byte."""
    args = ("acc", "Acc", [("gain", "double", "0.0")])
    kw = dict(init_params=[("mode", "int", "0")], csym="acc")
    assert make_state_ctx(*args, **kw) == make_state_ctx(
        *args, **kw, create_why=False
    )
    plain = make_errors_ctx("acc", "ValueError", "bad", csym="acc")
    assert plain == make_errors_ctx(
        "acc", "ValueError", "bad", csym="acc", why=False
    )
    assert "_why" not in plain["create_fail_block"]


# -- built and run -------------------------------------------------------------


def _body(fn: str) -> str:
    """A constructor that refuses mode 1 with a sentence, mode 2 without."""
    state_t = fn.rsplit("_create", 1)[0] + "_state_t"
    return (
        f"{fn}(int mode, const char **why)\n{{\n"
        "    if (mode == 1) {\n"
        f'        if (why)\n            *why = "{SAID[fn]}";\n'
        "        return NULL;\n    }\n"
        "    if (mode == 2)\n        return NULL;\n"
        f"    return calloc(1, sizeof({state_t}));\n}}"
    )


def _give_bodies(path: Path, fns: "list[str]") -> None:
    """Replace each scaffolded definition of *fns* with `_body`."""
    text = path.read_text("utf-8")
    for fn in fns:
        pat = re.compile(
            rf"^{re.escape(fn)}\(int mode, const char \*\*why\)\n\{{.*?^\}}",
            re.M | re.S,
        )
        text, n = pat.subn(lambda _m, fn=fn: _body(fn), text)
        assert n == 1, (path, fn, text)
    path.write_text(text, "utf-8")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj = _project(tmp_path_factory.mktemp("gh2552b"))
    _give_bodies(proj / "native/src/cor/cor_core.c", ["rs_cor_create"])
    _give_bodies(
        proj / "native/src/acc/acc_core.c",
        ["rs_acc_create", "rs_acc_create_burst"],
    )
    hdr = proj / "native/inc/rs/hdr/hdr_core.h"
    text = hdr.read_text("utf-8")
    text, n = re.subn(
        r"^rs_hdr_create\(int mode, const char \*\*why\)\n\{.*?^\}",
        lambda _m: _body("rs_hdr_create"),
        text,
        flags=re.M | re.S,
    )
    assert n == 1, text
    hdr.write_text(text, "utf-8")
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
        ["ctest", "--test-dir", "build", "--output-on-failure"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    return proj


_PROBE = """
import json, sys
sys.path.insert(0, "src")
from rs import Cor, Hdr
from rs.dsp import Acc, AccBurst


def make(cls, mode):
    try:
        cls(mode=mode)
        return ["ok"]
    except Exception as e:
        return [type(e).__name__, str(e)]


out = {c.__name__: {str(m): make(c, m) for m in (0, 1, 2)}
       for c in (Cor, Hdr, Acc, AccBurst)}
try:
    Cor(mode=0, why=None)
    out["why_kw"] = "accepted"
except TypeError:
    out["why_kw"] = "TypeError"
out["docs"] = {c.__name__: c.__doc__ for c in (Cor, Acc, AccBurst)}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(built) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=built,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize(
    "cls, exc, fn",
    [("Cor", "ValueError", "rs_cor_create"),
     ("Hdr", "ValueError", "rs_hdr_create"),
     ("Acc", "RuntimeError", "rs_acc_create"),
     ("AccBurst", "RuntimeError", "rs_acc_create_burst")],
)  # fmt: skip
def test_a_refusal_with_a_sentence_raises_it(
    probe: dict, cls: str, exc: str, fn: str
) -> None:
    """Standalone, header-only, module object and view alike: the
    declared class, the constructor's own text."""
    assert probe[cls]["0"] == ["ok"]
    assert probe[cls]["1"] == [exc, SAID[fn]]


@pytest.mark.parametrize(
    "cls, exc, msg",
    [("Cor", "ValueError", COR_MSG), ("Hdr", "ValueError", COR_MSG),
     ("Acc", "RuntimeError", ACC_MSG),
     ("AccBurst", "RuntimeError", ACC_MSG)],
)  # fmt: skip
def test_a_refusal_with_no_sentence_keeps_the_message(
    probe: dict, cls: str, exc: str, msg: str
) -> None:
    assert probe[cls]["2"] == [exc, msg]


def test_why_is_not_a_python_argument(probe: dict) -> None:
    assert probe["why_kw"] == "TypeError"


def test_standalone_and_module_document_alike(probe: dict) -> None:
    """One ``Raises`` entry, worded the same on every face."""
    for cls, exc, msg in (
        ("Cor", "ValueError", COR_MSG),
        ("Acc", "RuntimeError", ACC_MSG),
        ("AccBurst", "RuntimeError", ACC_MSG),
    ):
        doc = " ".join(probe["docs"][cls].split())
        assert (
            f"{exc} If construction fails. The exception message is the "
            f"reason the constructor gives, or ``{msg}`` when it gives none."
        ) in doc, (cls, doc)
