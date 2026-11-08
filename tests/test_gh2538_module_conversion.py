"""gh-2538: a property moved to ``getter_status`` on a module object.

A module object's binding fragment is sacred (gh-770): `apply` keeps every
wrapper body it finds. A property moved from ``expr`` or ``field`` to
``getter_status`` -- doppler's ``eta``, on two module objects -- kept its
old wrapper: the read went on returning the expression and never raised,
while the header, the stub and the ``.pyi`` said it would, and
``status --check`` passed.

jm cannot tell its own old render from an author's edit by reading the
text (an earlier cut of this fix tried, and reverted author edits), so it
does not rewrite the wrapper. It refuses: `apply` names each wrapper that
ignores a status the manifest declares and the fix -- delete the wrapper
and its ``PyGetSetDef`` row, re-run `apply` -- and ``status --check``
counts it (`_docsync.property_status_breaks`). An author's getter left in
the other prototype shape is refused too (`_property.getter_shape_mismatch`).

And nothing an author wrote moves: the regressions below are the three
edits that earlier cut overwrote, each required to survive `apply` byte
for byte.

Built and run where it matters: whether a read raises is a fact about the
extension.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

MSG = "a is search-only"
FIX = "delete each wrapper and its row in the PyGetSetDef table"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _rows(rows: dict) -> str:
    return "".join(
        f'\n[[acc.properties]]\nname = "{n}"\n{body}'
        for n, body in rows.items()
    )


def _module_project(root: Path, name: str) -> Path:
    _ok("new", name, cwd=root)
    proj = root / name
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", "--state", "mode:int:0",
        "--arg-type", "float", "--return-type", "float",
        cwd=proj)  # fmt: skip
    return proj


def _drop_member(ext: Path, comp: str, name: str) -> None:
    """Delete property *name*'s getter wrapper and its PyGetSetDef row --
    the fix the refusal names."""
    text = ext.read_text("utf-8")
    start = text.index(f"static PyObject *\n{comp}_getprop_{name}(")
    text = text[:start] + text[text.index("\n}\n", start) + 3 :]
    row = text.index(f'    {{ "{name}", (getter){comp}_getprop_{name},')
    text = text[:row] + text[text.index("\n", row) + 1 :]
    ext.write_text(text, "utf-8")


def _status_stub(fn: str, ctype: str) -> str:
    return (
        f"{fn}(const rc_acc_state_t *state, {ctype} *out)\n{{\n"
        "    (void)state; (void)out;\n"
        "    return 0; /* placeholder */\n}"
    )


# ── doppler's path, end to end ───────────────────────────────────────────────

BEFORE = {
    "a": 'type = "int"\nexpr = "self->handle->mode"\n',
    "b": 'type = "double"\nfield = true\n',
}
AFTER = {
    "a": (
        'type = "int"\ngetter_status = true\n'
        f'error = "AttributeError"\nerror_message = "{MSG}"\n'
    ),
    "b": 'type = "double"\ngetter_status = true\n',
}


@pytest.fixture(scope="module")
def doppler_path(tmp_path_factory) -> dict:
    """``expr`` and ``field`` properties on a module object, moved to
    getter_status: what `status --check` and `apply` say, then the named
    fix applied, then the project built."""
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj = _module_project(tmp_path_factory.mktemp("gh2538conv"), "rc")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(base + _rows(BEFORE), "utf-8")
    _ok("apply", cwd=proj)
    frag.write_text(base + _rows(AFTER), "utf-8")
    ext = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    out: dict = {"proj": proj, "ext_before": ext.read_text("utf-8")}
    out["status"] = run_cli("status", "--check", cwd=proj)
    out["refused"] = run_cli("apply", cwd=proj)
    out["ext_after_refusal"] = ext.read_text("utf-8")
    if out["refused"].returncode == 0:
        return out  # asserted by test_apply_refuses_naming_the_fix
    for name in "ab":
        _drop_member(ext, "Acc", name)
    out["fixed"] = run_cli("apply", cwd=proj)
    if out["fixed"].returncode != 0:
        return out  # asserted by test_the_named_fix_applies
    core = proj / "native" / "src" / "acc" / "acc_core.c"
    for fn, ctype, fail, value in (
        ("rc_acc_get_a", "int", 5, "11"),
        ("rc_acc_get_b", "double", 8, "2.5"),
    ):
        _replace_once(
            core,
            _status_stub(fn, ctype),
            f"{fn}(const rc_acc_state_t *state, {ctype} *out)\n{{\n"
            f"    if (state->mode != 0)\n        return {fail};\n"
            f"    *out = {value};\n    return 0;\n}}",
        )
    out["clean"] = run_cli("status", "--check", cwd=proj)
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        if r.returncode != 0:
            out["build"] = r.stdout + r.stderr
            return out
    out["build"] = ""
    return out


def test_status_check_counts_the_wrapper_before_apply(doppler_path):
    r = doppler_path["status"]
    assert r.returncode != 0, r.stdout + r.stderr
    for n in "ab":
        assert f"Acc_getprop_{n} does not test rc_acc_get_{n}()'s return" in (
            r.stdout
        ), r.stdout


def test_apply_refuses_naming_the_fix(doppler_path):
    r = doppler_path["refused"]
    assert r.returncode == 1, r.stdout + r.stderr
    for n in "ab":
        assert f"Acc_getprop_{n} does not test rc_acc_get_{n}()'s" in r.stderr
    assert FIX in r.stderr, r.stderr
    assert doppler_path["ext_after_refusal"] == doppler_path["ext_before"]


def test_the_named_fix_applies(doppler_path):
    r = doppler_path.get("fixed")
    assert r is not None and r.returncode == 0, r and r.stdout + r.stderr
    assert doppler_path["clean"].returncode == 0, (
        doppler_path["clean"].stdout + doppler_path["clean"].stderr
    )
    assert doppler_path["build"] == "", doppler_path["build"]


_PROBE = """
import json, sys
sys.path.insert(0, "src")
from rc.dsp import Acc


def read(obj, name):
    try:
        return ["ok", getattr(obj, name)]
    except Exception as e:
        return [type(e).__name__, str(e)]


out = {str(m): {n: read(Acc(m), n) for n in "ab"} for m in (0, 1)}
out["docs"] = {n: getattr(Acc, n).__doc__ for n in "ab"}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(doppler_path) -> dict:
    assert doppler_path.get("build") == "", "the fixed project did not build"
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=doppler_path["proj"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_after_the_fix_the_read_raises(probe):
    assert probe["0"] == {"a": ["ok", 11], "b": ["ok", 2.5]}
    assert probe["1"]["a"] == ["AttributeError", f"{MSG} (rc=5)"]
    assert probe["1"]["b"] == ["ValueError", "rc_acc_get_b failed (rc=8)"]


def test_after_the_fix_the_runtime_doc_has_raises(probe):
    """The row was rendered fresh with the wrapper, doc slot included."""
    for n in "ab":
        assert "Raises\n------\n" in probe["docs"][n], probe["docs"][n]


# ── the only finding gates `--check` ─────────────────────────────────────────


def test_status_check_exits_nonzero_on_the_property_finding_alone(tmp_path):
    """An up-to-date project whose one deviation is a wrapper that ignores
    a declared status: `--check`'s exit code is that finding's alone."""
    proj = _module_project(tmp_path, "rx")
    frag = proj / "objects" / "acc.toml"
    frag.write_text(
        frag.read_text("utf-8")
        + _rows({"s": 'type = "int"\ngetter_status = true\n'}),
        "utf-8",
    )
    _ok("apply", cwd=proj)
    clean = run_cli("status", "--check", cwd=proj)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    # The call stays (so nothing else about the fragment moves: the
    # link-check test names the same symbols), but as a bare statement
    # whose status is thrown away.
    _replace_once(
        proj / "native" / "src" / "dsp" / "dsp_ext_acc.c",
        "    int _rc = rx_acc_get_s(self->handle, &_got);\n",
        "    rx_acc_get_s(self->handle, &_got);\n    int _rc = 0;\n",
    )
    r = run_cli("status", "--check", cwd=proj)
    assert "PROPERTY STATUS (1)" in r.stdout, r.stdout
    assert r.returncode == 1, r.stdout + r.stderr


# ── an author's getter left in the other prototype shape ─────────────────────


@pytest.mark.parametrize(
    "before, after, needle",
    [
        (
            'type = "double"\n',
            'type = "double"\ngetter_status = true\n',
            "declares getter_status, so its binding calls rh_acc_get_p()"
            " as `int rh_acc_get_p(const rh_acc_state_t *state,"
            " double *out)`",
        ),
        (
            'type = "double"\ngetter_status = true\n',
            'type = "double"\n',
            "does not declare getter_status, so its binding calls"
            " rh_acc_get_p() as `double rh_acc_get_p(const rh_acc_state_t"
            " *state)`",
        ),
    ],
    ids=["to-status", "from-status"],
)
def test_a_getter_left_in_the_old_shape_is_refused(
    tmp_path, before, after, needle
):
    """A getter the author declared in one shape, whose row moved to the
    other: the build would fail on jm's call, so `apply` names the
    prototype to write instead, and writes nothing."""
    proj = _module_project(tmp_path, "rh")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(base + _rows({"p": before}), "utf-8")
    _ok("apply", cwd=proj)
    frag.write_text(base + _rows({"p": after}), "utf-8")
    ext = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    was = ext.read_text("utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert needle in r.stderr, r.stderr
    assert ext.read_text("utf-8") == was
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr


def test_a_plain_getter_wrapper_is_refused_once_the_c_moves(tmp_path):
    """plain -> getter_status with the author's C moved to the status
    shape: the kept wrapper still calls the getter in the one-argument
    value shape, so `apply` refuses it rather than leave a build that
    fails on jm's call."""
    proj = _module_project(tmp_path, "rp")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(base + _rows({"c": 'type = "double"\n'}), "utf-8")
    _ok("apply", cwd=proj)
    frag.write_text(
        base + _rows({"c": 'type = "double"\ngetter_status = true\n'}),
        "utf-8",
    )
    _replace_once(
        proj / "native" / "inc" / "rp" / "acc" / "acc_core.h",
        "double rp_acc_get_c(const rp_acc_state_t *state);",
        "int rp_acc_get_c(const rp_acc_state_t *state, double *out);",
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Acc_getprop_c does not test rp_acc_get_c()'s return" in r.stderr


# ── what the author wrote survives apply ─────────────────────────────────────


@pytest.mark.parametrize(
    "row, old, new",
    [
        (
            'type = "int"\nexpr = "self->handle->mode"\n',
            "return PyLong_FromLong((long)(self->handle->mode));",
            "return PyLong_FromLong((long)(self->handle->mode"
            " + my_side_effect(self)));",
        ),
        (
            'type = "double"\nwritable = true\nerror = "ValueError"\n',
            'PyErr_Format(PyExc_ValueError, "%s (rc=%lld)",',
            'PyErr_Format(PyExc_OverflowError, "%s (rc=%lld)",',
        ),
        (
            'type = "double"\n',
            '"P.\\n"',
            '"P.\\n"\n     "\\n"\n     "Raises\\n"\n     "------\\n"\n'
            '     "OSError\\n"\n     "    When the device is gone.\\n"',
        ),
    ],
    ids=["expr-getter", "setter-exception-class", "appended-raises-doc"],
)
def test_an_author_edit_survives_apply(tmp_path, row, old, new):
    """With no manifest change, `apply` keeps a module fragment's wrapper
    and doc slot byte for byte, however much they look like jm's."""
    proj = _module_project(tmp_path, "ra")
    frag = proj / "objects" / "acc.toml"
    frag.write_text(frag.read_text("utf-8") + _rows({"p": row}), "utf-8")
    _ok("apply", cwd=proj)
    ext = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    _replace_once(ext, old, new)
    edited = ext.read_text("utf-8")
    _ok("apply", cwd=proj)
    assert ext.read_text("utf-8") == edited


# ── a wrapper the author wrote ────────────────────────────────────────────────


HAND = (
    "    long twice = 2 * (long)self->handle->mode;\n"
    "    return PyLong_FromLong(twice);\n"
)


@pytest.fixture()
def hand_written(tmp_path) -> Path:
    """A module object whose `g` getter the author rewrote in the fragment,
    and whose row then moves to getter_status."""
    proj = _module_project(tmp_path, "rh")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(
        base + _rows({"g": 'type = "int"\nexpr = "self->handle->mode"\n'}),
        "utf-8",
    )
    _ok("apply", cwd=proj)
    ext = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    _replace_once(
        ext, "    return PyLong_FromLong((long)(self->handle->mode));\n", HAND
    )
    frag.write_text(
        base + _rows({"g": 'type = "int"\ngetter_status = true\n'}), "utf-8"
    )
    return proj


def test_apply_refuses_a_hand_written_wrapper_that_drops_the_status(
    hand_written,
):
    ext = hand_written / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    before = ext.read_text("utf-8")
    r = run_cli("apply", cwd=hand_written)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Acc_getprop_g does not test rh_acc_get_g()'s return" in r.stderr
    assert FIX in r.stderr, r.stderr
    assert ext.read_text("utf-8") == before
