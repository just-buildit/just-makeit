"""gh-2555: `jm apply` re-renders a module constructor's diagnostics.

A module fragment is sacred, so `apply` keeps its ``tp_init``. But the
create-failure block (``create_error``, ``create_error_message``), the
post-construction warnings (``[[<obj>.warnings]]``) and the ``_why`` local
(``create_why``) are the manifest's and nothing else. Edited in the
manifest, they reached the ``.pyi`` while the binding went on raising the
old exception, and ``status --check`` passed.

Now a constructor that is jm's render but for those is re-rendered, the
class ``__doc__`` beside it too; one edited elsewhere is reported, gating.
The built project is the proof: the manifest's new exception and warning
are what Python sees.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _docsync

#: Each class's diagnostics before and after the manifest edit. ``Pulse``
#: declares none of its own and inherits ``acc``'s.
OLD = {
    "Acc": ("ValueError", "old acc", "UserWarning", "old acc warn"),
    "Burst": ("ValueError", "old burst", "UserWarning", "old burst warn"),
    "Pulse": ("ValueError", "old acc", None, None),
}
NEW = {
    cls: tuple(
        None
        if v is None
        else v.replace("old ", "new ")
        .replace("ValueError", "RuntimeError")
        .replace("UserWarning", "RuntimeWarning")
        for v in vals
    )
    for cls, vals in OLD.items()
}


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _frag(proj: Path, frag_id: str) -> Path:
    return proj / "native" / "src" / "dsp" / f"dsp_ext_{frag_id}.c"


def _declare(root: Path) -> Path:
    """Module ``dsp``'s ``acc`` and its views ``Burst`` and ``Pulse``,
    with the OLD diagnostics declared through the verbs."""
    _ok("new", "p", cwd=root)
    proj = root / "p"
    _ok("module", "dsp", cwd=proj)
    _ok(
        "object", "acc", "--module", "dsp", "--state", "flag:bool:false",
        "--init-param", "mode:int:0", cwd=proj,
    )  # fmt: skip
    for view in ("Burst", "Pulse"):
        _ok(
            "view", "acc", view, "--module", "dsp",
            "--create-fn", f"acc_create_{view.lower()}", cwd=proj,
        )  # fmt: skip
    for cls, view in (("Acc", ()), ("Burst", ("--view", "Burst"))):
        cat, msg, wcat, wmsg = OLD[cls]
        _ok(
            "error", "acc", "--module", "dsp", *view,
            "--category", cat, "--message", msg, cwd=proj,
        )  # fmt: skip
        _ok(
            "warning", "acc", "--module", "dsp", *view, "--condition",
            "flag", "--category", wcat, "--message", wmsg, cwd=proj,
        )  # fmt: skip
    return proj


def _edit_manifest(proj: Path) -> None:
    """Every OLD diagnostic made NEW, in the manifest alone."""
    path = proj / "objects" / "acc.toml"
    text = path.read_text("utf-8")
    for cls, vals in OLD.items():
        for old, new in zip(vals, NEW[cls]):
            if old is not None:
                text = text.replace(f'"{old}"', f'"{new}"')
    path.write_text(text, "utf-8")


@pytest.fixture(scope="module")
def edited(tmp_path_factory) -> Path:
    """The project after the manifest edit, before any `apply`."""
    proj = _declare(tmp_path_factory.mktemp("gh2555"))
    _edit_manifest(proj)
    return proj


def test_status_check_names_the_stale_constructors(edited: Path) -> None:
    """Before `apply`, `status --check` fails and says which constructor
    raises what; it used to pass once the `.pyi` was in step."""
    r = run_cli("status", "--check", cwd=edited)
    assert r.returncode != 0, r.stdout
    assert "DIAGNOSTICS (3)" in r.stdout, r.stdout
    assert 'ValueError "old acc"' in r.stdout, r.stdout


# -- built and run -------------------------------------------------------------

_BODY = """{
    if (mode == 1)
        return NULL;
    p_acc_state_t *obj = calloc(1, sizeof(*obj));
    if (obj)
        obj->flag = (mode == 2);
    return obj;
}"""


def _give_bodies(core_c: Path) -> None:
    """Each constructor refuses mode 1 and sets ``flag`` under mode 2."""
    text = core_c.read_text("utf-8")
    for fn in ("p_acc_create", "acc_create_burst", "acc_create_pulse"):
        text, n = re.subn(
            rf"^({fn}\(int mode\)\n)\{{.*?^\}}",
            lambda m: m.group(1) + _BODY,
            text,
            flags=re.M | re.S,
        )
        assert n == 1, (fn, text)
    core_c.write_text(text, "utf-8")


_PROBE = """
import json, sys, warnings
sys.path.insert(0, "src")
from p.dsp import Acc, Burst, Pulse

out = {}
for cls in (Acc, Burst, Pulse):
    try:
        cls(mode=1)
        raised = None
    except Exception as e:
        raised = [type(e).__name__, str(e)]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cls(mode=2)
    warned = [[x.category.__name__, str(x.message)] for x in w]
    out[cls.__name__] = {"raised": raised, "warned": warned,
                         "doc": cls.__doc__}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def applied(tmp_path_factory) -> "tuple[Path, str]":
    proj = _declare(tmp_path_factory.mktemp("gh2555a"))
    _edit_manifest(proj)
    out = _ok("apply", cwd=proj)
    return proj, out


@pytest.fixture(scope="module")
def probe(applied) -> dict:
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj, _ = applied
    _give_bodies(proj / "native" / "src" / "acc" / "acc_core.c")
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_apply_names_each_constructor_it_rerenders(applied) -> None:
    _, out = applied
    for frag_id in ("acc", "burst", "pulse"):
        assert f"dsp_ext_{frag_id}.c: constructor (" in out, out


def test_status_check_is_clean_after_apply(applied) -> None:
    proj, _ = applied
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("cls", sorted(NEW))
def test_the_built_class_raises_the_new_exception(
    probe: dict, cls: str
) -> None:
    cat, msg, _, _ = NEW[cls]
    assert probe[cls]["raised"] == [cat, msg]


@pytest.mark.parametrize("cls", sorted(NEW))
def test_the_built_class_warns_the_new_warning(probe: dict, cls: str) -> None:
    _, _, wcat, wmsg = NEW[cls]
    assert probe[cls]["warned"] == ([[wcat, wmsg]] if wcat else [])


@pytest.mark.parametrize("cls", sorted(NEW))
def test_help_documents_the_new_diagnostics(probe: dict, cls: str) -> None:
    """The class `__doc__` is the `.pyi`'s again: its `Raises` and `Warns`
    were the only difference, so it is jm's render for the old manifest."""
    doc = probe[cls]["doc"]
    for old, new in zip(OLD[cls], NEW[cls]):
        if old is not None and old != new:
            assert old not in doc, doc
            assert new in doc, doc


# -- what apply leaves alone ---------------------------------------------------


def test_a_constructor_edited_elsewhere_is_reported_not_rewritten(
    tmp_path,
) -> None:
    """An edit outside the diagnostics makes the constructor the author's:
    `apply` keeps it, warns, and `status --check` fails on it."""
    proj = _declare(tmp_path)
    frag = _frag(proj, "acc")
    text = frag.read_text("utf-8")
    assert text.count("    return 0;\n}") == 1, text
    text = text.replace("    return 0;\n}", "    (void)0;\n    return 0;\n}")
    frag.write_text(text, "utf-8")
    _edit_manifest(proj)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "constructor's diagnostics disagree" in r.stderr, r.stderr
    after = frag.read_text("utf-8")
    assert '"old acc"' in after and "(void)0;" in after
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout
    assert "DIAGNOSTICS (1)" in r.stdout, r.stdout


def test_a_formatted_constructor_is_still_recognised(tmp_path) -> None:
    """GNU layout -- a space before every `(`, braces on their own lines --
    changes no token, so the constructor is still jm's."""
    proj = _declare(tmp_path)
    frag = _frag(proj, "acc")
    text = frag.read_text("utf-8")
    body = _docsync._init_body(text)
    gnu = re.sub(r"(\w)\(", r"\1 (", body)
    gnu = re.sub(r"\) \{\n", ")\n    {\n", gnu)
    assert gnu != body
    frag.write_text(text.replace(body, gnu, 1), "utf-8")
    _edit_manifest(proj)
    out = _ok("apply", cwd=proj)
    assert "dsp_ext_acc.c: constructor (" in out, out
    assert '"new acc"' in frag.read_text("utf-8")


def test_create_why_reaches_the_constructor(tmp_path) -> None:
    """``create_why`` switched on in the manifest: the `_why` local and the
    argument passing it reach the kept constructor, as a verb renders them.
    """
    proj = _declare(tmp_path)
    path = proj / "objects" / "acc.toml"
    text = path.read_text("utf-8")
    assert text.count('create_error_message = "old acc"\n') == 1, text
    path.write_text(
        text.replace(
            'create_error_message = "old acc"\n',
            'create_error_message = "old acc"\ncreate_why = true\n',
        ),
        "utf-8",
    )
    out = _ok("apply", cwd=proj)
    assert "create_why is on in the manifest, off here" in out, out
    init = _docsync._init_body(_frag(proj, "acc").read_text("utf-8"))
    assert "const char *_why = NULL;" in init, init
    assert re.search(r"p_acc_create\(\w+, &_why\)", init), init
