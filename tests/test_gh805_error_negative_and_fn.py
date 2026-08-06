"""gh-805 §A2 + §B — a per-method C-symbol override, and value-or-error.

Two keys from doppler's telemetry migration off ``no_generate``, filed
together because they are the same problem seen twice: **jm has the
information to generate the member, and the manifest has no way to say it**,
so the project hand-writes that binding — and a hand-written binding stops
receiving every future codegen fix.

**§B — ``error_negative``.** jm could already translate an int return into an
exception three ways (``status_return``, ``check_return``, destroy's
``error``), and all three assume the int carries *nothing but* status. That
cannot express the most common convention in C:

    the return is a VALUE, unless it is negative, in which case it is an
    error code

``open``, ``read``, ``snprintf`` and every registry-style ``..._lookup()``
work this way. Without it, ``probe_id("nope")`` returns ``-4`` — shaped
exactly like a probe id, so the caller stores it and emits into it forever and
the failure surfaces arbitrarily far away as bad data.

``status_return`` cannot be stretched to cover it: the int IS the id on
success, so "non-zero raises" rejects every successful call but id 0. The two
keys make opposite claims about the same int and are rejected together.

**§A2 — ``fn``.** Every object symbol is derived from the component, and
``create_fn`` was the only escape hatch. The concrete need is a *validating
variant of a hot-path function*: ``dp_tlm_emit`` is the inline, unchecked,
per-event emit, and a binding must not call it with a caller-supplied id —
that is what segfaulted the interpreter from pure Python, because the inline C
indexes a fixed ``probes[64]`` on a documented "the id came from probe()"
contract a binding cannot honour. The right C API is *both* functions, and the
right binding is the checked one exposed under the plain Python name.

``fn`` is already the spelling for properties, getters, setters, composer
fields and handle methods, so this is one key reaching one more place.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _script  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

COMP = "dp_tlm"
MOD = "telemetry"


def _project(tmp_path: Path) -> Path:
    """A module object named for its C prefix, as doppler's telemetry is."""
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        module_run(root, MOD)
        object_run(
            root,
            COMP,
            MOD,
            state_vars=[("cap", "size_t", "64")],
            arg_type="void",
            return_type="void",
            no_step=True,
            class_name="Telemetry",
        )
    return root


def _add(root: Path, name: str, **kw) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        method_run(
            root,
            COMP,
            name,
            MOD,
            kw.pop("arg_type", "void"),
            kw.pop("return_type", "int"),
            False,
            [],
            **kw,
        )


def _core_h(root: Path) -> str:
    return (root / "native" / "inc" / COMP / f"{COMP}_core.h").read_text()


def _core_c(root: Path) -> str:
    return (root / "native" / "src" / COMP / f"{COMP}_core.c").read_text()


def _glue(root: Path) -> str:
    p = root / "native" / "src" / MOD / f"{MOD}_ext_{COMP}.c"
    if not p.exists():
        p = root / "native" / "src" / MOD / f"{MOD}_ext.c"
    return p.read_text()


def _pyi(root: Path) -> str:
    return (root / "src" / "proj" / MOD / f"{MOD}.pyi").read_text()


class TestErrorNegativeEmission:
    """§B: the int is the value; only `< 0` raises."""

    def test_negative_raises_the_declared_exception(self, tmp_path):
        root = _project(tmp_path)
        _add(
            root,
            "probe_id",
            params=[("name", "const char *")],
            error_negative=True,
            error="KeyError",
            error_message="no probe by that name",
        )
        g = _glue(root)
        assert "if (_rc < 0) {" in g
        assert "PyErr_Format(PyExc_KeyError," in g
        assert '"no probe by that name (rc=%d)"' in g

    def test_success_returns_the_value_not_none(self, tmp_path):
        """The distinction from `status_return`, and the whole point: a
        successful call still has a number to give back."""
        root = _project(tmp_path)
        _add(root, "probe_id", error_negative=True)
        g = _glue(root)
        assert "return PyLong_FromLong((long)_rc);" in g
        assert "Py_RETURN_NONE;" not in g.split("probe_id")[-1][:400]

    def test_pyi_return_annotation_is_int_not_none(self, tmp_path):
        """`status_return` forces `-> None`; this must NOT. Guards the
        `_stubs.py` branch that sits directly beside it."""
        root = _project(tmp_path)
        _add(root, "probe_id", error_negative=True)
        assert "def probe_id(self) -> int:" in _pyi(root)

    def test_default_exception_is_value_error(self, tmp_path):
        root = _project(tmp_path)
        _add(root, "probe_id", error_negative=True)
        assert "PyErr_Format(PyExc_ValueError," in _glue(root)


class TestErrorNegativeRejections:
    """Each of these otherwise produces C that COMPILES and is wrong."""

    def test_rejects_status_return_together(self, tmp_path):
        root = _project(tmp_path)
        with (
            pytest.raises(SystemExit),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            _add(root, "x", error_negative=True, status_return=True)
        assert "opposite claims" in err.getvalue()

    @pytest.mark.parametrize("rt", ["size_t", "uint32_t", "double", "bool"])
    def test_rejects_a_return_that_cannot_be_negative(self, tmp_path, rt):
        """`kind == "int"` is NOT the test — it is true of `size_t` and every
        `uint*_t`, which is exactly the set where `_rc < 0` is always false.
        Always-false is the failure that compiles and runs."""
        root = _project(tmp_path)
        with (
            pytest.raises(SystemExit),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            _add(root, "x", return_type=rt, error_negative=True)
        msg = err.getvalue()
        assert "SIGNED integer return type" in msg
        assert rt in msg

    def test_rejects_error_without_error_negative(self, tmp_path):
        root = _project(tmp_path)
        with (
            pytest.raises(SystemExit),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            _add(root, "x", error="KeyError")
        assert "needs\n--error-negative" in err.getvalue()

    def test_rejects_an_unknown_exception_category(self, tmp_path):
        root = _project(tmp_path)
        with (
            pytest.raises(SystemExit),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            _add(root, "x", error_negative=True, error="NotAnException")
        assert "not a known exception category" in err.getvalue()


class TestFnOverride:
    """§A2: the C symbol moves; the Python face does not."""

    def test_the_sacred_header_declares_the_override(self, tmp_path):
        """The half that must be right — the definition is written to it."""
        root = _project(tmp_path)
        _add(root, "emit", params=[("id", "int")], fn="dp_tlm_emit_checked")
        assert "int dp_tlm_emit_checked(" in _core_h(root)
        assert "int dp_tlm_emit(" not in _core_h(root)

    def test_the_core_c_definition_matches_the_declaration(self, tmp_path):
        root = _project(tmp_path)
        _add(root, "emit", params=[("id", "int")], fn="dp_tlm_emit_checked")
        assert "dp_tlm_emit_checked(dp_tlm_state_t *state" in _core_c(root)

    def test_the_glue_calls_the_override(self, tmp_path):
        root = _project(tmp_path)
        _add(root, "emit", params=[("id", "int")], fn="dp_tlm_emit_checked")
        assert "dp_tlm_emit_checked(self->handle" in _glue(root)

    def test_the_python_name_is_unchanged(self, tmp_path):
        """`fn` is a C-side override only. A PyMethodDef row or a stub naming
        the C symbol would make the override visible to callers, which is the
        opposite of adopting existing C behind a chosen Python API."""
        root = _project(tmp_path)
        _add(root, "emit", params=[("id", "int")], fn="dp_tlm_emit_checked")
        assert '{"emit", ' in _glue(root)
        assert "def emit(" in _pyi(root)
        assert "dp_tlm_emit_checked" not in _pyi(root)

    def test_without_fn_the_symbol_is_still_derived(self, tmp_path):
        """Zero churn for every manifest that does not use the key."""
        root = _project(tmp_path)
        _add(root, "emit", params=[("id", "int")])
        assert "int dp_tlm_emit(" in _core_h(root)


class TestTheThreeWriters:
    """`jm <cmd>`, `jm apply` and `jm script` must agree. `_apply` and
    `_script` enumerate method keys ONE BY ONE, so a key neither names is
    silently absent — which is how an earlier key made `apply` rewrite the
    sacred prototype to the wrong shape."""

    def _built(self, tmp_path):
        root = _project(tmp_path)
        _add(
            root,
            "probe_id",
            params=[("name", "const char *")],
            error_negative=True,
            error="KeyError",
            error_message="no probe by that name",
        )
        _add(root, "emit", params=[("id", "int")], fn="dp_tlm_emit_checked")
        return root

    def test_the_manifest_records_every_key(self, tmp_path):
        cfg = C.load(self._built(tmp_path))
        by = {m["name"]: m for m in C.methods(cfg, COMP)}
        assert by["probe_id"]["error_negative"] is True
        assert by["probe_id"]["error"] == "KeyError"
        assert by["probe_id"]["error_message"] == "no probe by that name"
        assert by["emit"]["fn"] == "dp_tlm_emit_checked"

    def test_apply_does_not_rewrite_the_sacred_prototype(self, tmp_path):
        root = self._built(tmp_path)
        before = _core_h(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        assert _core_h(root) == before

    def test_script_reconstructs_every_flag(self, tmp_path):
        root = self._built(tmp_path)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            _script.run(root)
        s = out.getvalue()
        assert "--error-negative" in s
        assert "--error KeyError" in s
        assert '--error-message "no probe by that name"' in s
        assert "--fn dp_tlm_emit_checked" in s

    def test_script_flags_carry_the_line_continuation(self, tmp_path):
        """A flag appended without `_flag`'s indent and trailing `\\` breaks
        the replayed script at that line — the bug this found in the
        `count_default` sibling next to it."""
        line = _script._method_flags(
            {"name": "x", "error_message": "no probe by that name"}, None
        )[-1]
        assert line.startswith("    --error-message ")
        assert line.endswith(" \\\n")


class TestScriptQuoting:
    """A C expression is not a bare shell word."""

    def test_a_redirect_in_a_value_is_quoted(self):
        """`state->num_taps` emitted unquoted is a REDIRECT: the replayed
        script wrote a file called `num_taps` and passed `state-` as the
        value. Found via `error_message`, fixed for every value."""
        assert _script._q("state->num_taps") == '"state->num_taps"'

    @pytest.mark.parametrize("bad", ["a|b", "a&b", "a;b", "a$b", "a`b"])
    def test_other_shell_actives_are_quoted(self, bad):
        assert _script._q(bad) == f'"{bad}"'

    def test_a_plain_word_is_left_alone(self):
        assert _script._q("float") == "float"
