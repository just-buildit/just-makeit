"""gh-2538: changing a property's backing reaches a module object's fragment.

A module object's binding fragment is sacred (gh-770): `apply` keeps every
wrapper body it finds. A property's getter and setter wrappers are pure
marshalling of the manifest row, so a row moved from ``expr`` (or
``field``, or a plain C getter) to ``getter_status`` kept the old body:
the read went on returning the expression and never raised, while the
header, the stub and the ``.pyi`` all said it would -- and
``status --check`` passed. doppler's ``eta`` is exactly that move, on two
module objects.

Now `apply` re-renders a wrapper that is jm's own render of another
backing of the same row (`_object._restyled_property_units`), refreshes
the runtime doc whose only change is its ``Raises`` section, and refuses a
hand-written wrapper that would drop a status the manifest declares
(`_docsync.property_status_breaks`); ``status --check`` counts both.

Built and run: whether a read raises is a fact about the extension.
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


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _status_stub(fn: str, ctype: str) -> str:
    return (
        f"{fn}(const rc_acc_state_t *state, {ctype} *out)\n{{\n"
        "    (void)state; (void)out;\n"
        "    return 0; /* placeholder */\n}"
    )


def _plain_stub(fn: str) -> str:
    return (
        f"{fn}(const rc_acc_state_t *state)\n{{\n"
        "    (void)state;\n"
        "    return 0; /* placeholder */\n}"
    )


#: Each property's row before and after, as manifest lines. ``mode`` is the
#: configuration: 0 a search flavor, anything else a map one.
BEFORE = {
    "a": 'type = "int"\nexpr = "self->handle->mode"\n',
    "b": 'type = "double"\nfield = true\n',
    "c": 'type = "double"\n',
    # A class other than the default, so recognising jm's old render does
    # not depend on which exception it raised.
    "d": 'type = "int"\ngetter_status = true\nerror = "KeyError"\n',
    "e": 'type = "int"\n',
    "f": 'type = "int"\nexpr = "self->handle->mode"\n',
}
AFTER = {
    "a": (
        'type = "int"\ngetter_status = true\n'
        f'error = "AttributeError"\nerror_message = "{MSG}"\n'
    ),
    "b": 'type = "double"\ngetter_status = true\n',
    "c": 'type = "double"\ngetter_status = true\nerror = "AttributeError"\n',
    "d": 'type = "int"\nexpr = "self->handle->mode + 40"\n',
    "e": 'type = "int"\nexpr = "self->handle->mode + 7"\n',
    "f": 'type = "int"\n',
}


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


@pytest.fixture(scope="module")
def converted(tmp_path_factory) -> "tuple[Path, str, str]":
    """Every backing move on a module object, applied over a fragment the
    first `apply` wrote: the status `--check` gave between the two applies,
    and what the second `apply` printed."""
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    proj = _module_project(tmp_path_factory.mktemp("gh2538conv"), "rc")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(base + _rows(BEFORE), "utf-8")
    _ok("apply", cwd=proj)
    frag.write_text(base + _rows(AFTER), "utf-8")
    # `c` had a plain C getter: its declaration and definition are the
    # author's to move to the status shape (`apply` refuses until they do;
    # `test_a_getter_left_in_the_old_shape_is_refused`).
    core = proj / "native" / "src" / "acc" / "acc_core.c"
    _replace_once(
        proj / "native" / "inc" / "rc" / "acc" / "acc_core.h",
        "double rc_acc_get_c(const rc_acc_state_t *state);",
        "int rc_acc_get_c(const rc_acc_state_t *state, double *out);",
    )
    _replace_once(
        core,
        "double\nrc_acc_get_c(const rc_acc_state_t *state)\n{\n"
        "    (void)state;\n    return 0.0; /* placeholder */\n}",
        "int\nrc_acc_get_c(const rc_acc_state_t *state, double *out)\n{\n"
        "    if (state->mode != 0)\n        return 6;\n"
        "    *out = 1.25;\n    return 0;\n}",
    )
    between = run_cli("status", "--check", cwd=proj)
    r = run_cli("apply", cwd=proj)
    applied = r.stdout + r.stderr
    if r.returncode != 0:
        # Asserted by `test_apply_names_each_rewritten_wrapper`, so a
        # refusal here reads as that test failing, not as a setup error.
        return proj, between.stdout + between.stderr, applied

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
    _replace_once(
        core,
        _plain_stub("rc_acc_get_f"),
        "rc_acc_get_f(const rc_acc_state_t *state)\n{\n"
        "    return state->mode + 3;\n}",
    )
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        if r.returncode != 0:
            # Asserted by `test_the_converted_project_builds`.
            (proj / "build.log").write_text(r.stdout + r.stderr, "utf-8")
            break
    return proj, between.stdout + between.stderr, applied


def test_the_converted_project_builds(converted):
    """A wrapper left calling the old getter shape fails here, on jm's
    call, rather than in the tests that read through it."""
    log = converted[0] / "build.log"
    assert not log.exists(), log.read_text("utf-8")


_PROBE = """
import json, sys
sys.path.insert(0, "src")
from rc.dsp import Acc


def read(obj, name):
    try:
        return ["ok", getattr(obj, name)]
    except Exception as e:
        return [type(e).__name__, str(e)]


out = {}
for mode in (0, 1):
    o = Acc(mode)
    out[str(mode)] = {n: read(o, n) for n in "abcdef"}
out["docs"] = {n: getattr(Acc, n).__doc__ for n in "abcdef"}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(converted) -> dict:
    assert (converted[0] / "build").is_dir(), "the second apply refused"
    assert not (converted[0] / "build.log").exists(), "the build failed"
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=converted[0],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_status_check_counts_the_stale_wrappers_before_apply(converted):
    _, between, _ = converted
    assert "PROPERTY STATUS" in between, between
    for n in "abcdef":
        assert f"Acc_getprop_{n} is jm's render of another backing" in (
            between
        ), between


def test_apply_names_each_rewritten_wrapper(converted):
    _, _, applied = converted
    assert "error:" not in applied, applied
    for n in "abcdef":
        assert f"Acc_getprop_{n} (backing changed)" in applied, applied


@pytest.mark.parametrize(
    "name, raised",
    [
        ("a", ["AttributeError", f"{MSG} (rc=5)"]),
        ("b", ["ValueError", "rc_acc_get_b failed (rc=8)"]),
        ("c", ["AttributeError", "rc_acc_get_c failed (rc=6)"]),
    ],
)
def test_a_wrapper_moved_to_getter_status_raises(probe, name, raised):
    """expr, field and plain getters, each moved to getter_status."""
    assert probe["0"][name][0] == "ok"
    assert probe["1"][name] == raised


def test_the_values_follow_the_new_backing(probe):
    """The read returns what the NEW row says: the status getter's value,
    the new expression, the plain getter's return."""
    assert [probe["0"][n][1] for n in "abc"] == [11, 2.5, 1.25]
    assert probe["0"]["d"] == ["ok", 40] and probe["1"]["d"] == ["ok", 41]
    assert probe["0"]["e"] == ["ok", 7] and probe["1"]["e"] == ["ok", 8]
    assert probe["0"]["f"] == ["ok", 3] and probe["1"]["f"] == ["ok", 4]


def test_the_runtime_doc_follows_the_raise(probe):
    """The Raises section arrives with the status and leaves without it."""
    docs = probe["docs"]
    for n in "abc":
        assert "Raises\n------\n" in docs[n], docs[n]
    for n in "def":
        assert "Raises" not in docs[n], docs[n]


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


def test_a_setter_moved_to_error_is_rerendered(tmp_path):
    """The setter half of the class: a writable property that gains
    `error` (gh-2182) gets jm's status-testing setter in place of the bare
    call jm wrote before, and loses it again when `error` goes."""
    proj = _module_project(tmp_path, "rs")
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    plain = 'type = "double"\nwritable = true\n'
    frag.write_text(base + _rows({"h": plain}), "utf-8")
    _ok("apply", cwd=proj)
    ext = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    frag.write_text(
        base + _rows({"h": plain + 'error = "ValueError"\n'}), "utf-8"
    )
    out = _ok("apply", cwd=proj)
    assert "Acc_setprop_h (backing changed)" in out, out
    assert "int _rc = rs_acc_set_h(self->handle, v);" in ext.read_text()
    frag.write_text(base + _rows({"h": plain}), "utf-8")
    _ok("apply", cwd=proj)
    assert "    rs_acc_set_h(self->handle, v);\n" in ext.read_text()


def test_a_view_fragment_follows_its_parent_row(tmp_path):
    """A view's fragment renders the parent's properties too, from its own
    rows (`_make_view_ctx`), so its wrapper moves with the row."""
    proj = _module_project(tmp_path, "rv")
    _ok("view", "acc", "Alt", "--module", "dsp", "--create-fn",
        "rv_acc_create_alt", cwd=proj)  # fmt: skip
    frag = proj / "objects" / "acc.toml"
    base = frag.read_text("utf-8")
    frag.write_text(base + _rows({"a": BEFORE["a"]}), "utf-8")
    _ok("apply", cwd=proj)
    frag.write_text(base + _rows({"a": AFTER["a"]}), "utf-8")
    out = _ok("apply", cwd=proj)
    assert "Alt_getprop_a (backing changed)" in out, out
    view = proj / "native" / "src" / "dsp" / "dsp_ext_alt.c"
    assert "int _rc = rv_acc_get_a(self->handle, &_got);" in view.read_text()


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
    assert "delete the wrapper and its row in the PyGetSetDef table" in (
        r.stderr
    )
    assert ext.read_text("utf-8") == before


def test_status_check_counts_it(hand_written):
    r = run_cli("status", "--check", cwd=hand_written)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "Acc_getprop_g does not test rh_acc_get_g()'s return" in r.stdout


def test_the_named_fix_works(hand_written):
    """Deleting the wrapper and its row, as the refusal says, lets `apply`
    put jm's back."""
    ext = hand_written / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    text = ext.read_text("utf-8")
    start = text.index("static PyObject *\nAcc_getprop_g(")
    end = text.index("}\n", start) + 2
    text = text[:start] + text[end:]
    row = text.index('    { "g", (getter)Acc_getprop_g,')
    text = text[:row] + text[text.index("\n", row) + 1 :]
    ext.write_text(text, "utf-8")
    _ok("apply", cwd=hand_written)
    assert "int _rc = rh_acc_get_g(self->handle, &_got);" in ext.read_text(
        "utf-8"
    )
