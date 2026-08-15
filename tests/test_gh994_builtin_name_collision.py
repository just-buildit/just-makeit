"""gh-994: a method may be named after a built-in, and jm must emit it once.

An object's Python surface is written down as ``[[<obj>.methods]]`` entries,
and doppler names one of those ``reset`` in 28 objects. jm answered that
collision by emitting *both* — the built-in's body and the method's stub,
both called ``<obj>_reset``, into a create-only ``_core.c`` — so the tree it
had just written did not compile.

The reason it shipped is the shape of the tests that were watching. Every one
of them read the generated text for a substring, and a substring assertion is
satisfied by the *first* of two definitions as happily as by the only one. The
compiler tier below is the gate that was missing: it hands ``_core.c`` and
``_ext.c`` to a real C compiler and asks whether jm's output is a translation
unit, which no amount of grepping can answer.

Two tiers, matching how this repo already splits build-dependent tests:

- the counting tier needs no toolchain and runs everywhere
- the compiler tier needs `cc`, and is the one that would have caught this

Both sweep the whole built-in class rather than the one name in the report.
``reset`` is what doppler hit; ``step``, ``steps``, ``create``, ``destroy``
and the ``get_``/``set_`` accessors of a state field were every bit as broken
and nobody had looked. Each is exercised on BOTH paths that write an object,
because they settle the collision differently by necessity — scaffolding from
a manifest jm knows about the method before it writes ``_core.c`` and lets it
win; adding one later, the built-in is already in a file jm must not rewrite.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

#: The generated CMake asks for C99 with GNU extensions on; compile jm's
#: output under the flags its real build uses, not stricter ones.
_STD = "-std=gnu99"

#: The state field every case declares, so the `get_`/`set_` accessor pair
#: exists to collide with.
_STATE = [("gain", "double", "1.0")]

#: (id, method name, arg_type, return_type, params, variable_output).
#:
#: `reset` twice on purpose: bare, it merely *names* the built-in — doppler's
#: entry, whose implied prototype is byte-identical — and parameterised it
#: *replaces* it. The two resolve in opposite directions and only one of them
#: was ever exercised.
_CASES = [
    pytest.param("reset", "void", "void", [], False, id="reset"),
    pytest.param(
        "reset",
        "void",
        "void",
        [("start", "uint32_t")],
        False,
        id="reset-with-params",
    ),
    pytest.param("create", "void", "void", [], False, id="create"),
    pytest.param("destroy", "void", "void", [], False, id="destroy"),
    pytest.param("step", "void", "float", [], False, id="step"),
    pytest.param("steps", "void", "float", [], False, id="steps"),
    pytest.param(
        "steps",
        "void",
        "float",
        [("x", "float[]")],
        True,
        id="steps-variable-output",
    ),
    pytest.param("get_gain", "void", "double", [], False, id="get-accessor"),
    pytest.param("set_gain", "double", "void", [], False, id="set-accessor"),
]


def _via_cli(dest: Path, name, arg_type, return_type, params, var_out) -> Path:
    """`jm object` then `jm method` — the built-in is already on disk."""
    new_run("proj", dest)
    object_run(dest, "osc", None, state_vars=_STATE)
    method_run(
        dest,
        "osc",
        name,
        None,
        arg_type,
        return_type,
        var_out,
        [],
        params=params,
    )
    return dest


def _via_apply(dest, name, arg_type, return_type, params, var_out) -> Path:
    """`jm apply` — the manifest names the method before `_core.c` exists."""
    lines = [
        "[osc]",
        'arg_type = "void"',
        'return_type = "float"',
        'mutable = "true"',
        "",
        "[[osc.state]]",
        'name = "gain"',
        'type = "double"',
        'default = "1.0"',
        "",
        "[[osc.methods]]",
        f'name = "{name}"',
        f'arg_type = "{arg_type}"',
        f'return_type = "{return_type}"',
    ]
    if var_out:
        lines.append("variable_output = true")
    if params:
        inner = ", ".join(f'{{name = "{n}", type = "{t}"}}' for n, t in params)
        lines.append(f"params = [{inner}]")
    frag = dest.parent / "frag.toml"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text("\n".join(lines) + "\n", encoding="utf-8")
    new_run("proj", dest)
    apply_run(dest, fragment=frag)
    return dest


_BUILDERS = pytest.mark.parametrize(
    "build", [_via_cli, _via_apply], ids=["cli", "apply"]
)


def _definitions_of(text: str, symbol: str) -> int:
    """How many times *symbol* is DEFINED in *text*.

    A definition starts at column 0 — jm puts the return type on its own line
    for some shapes and inline for others, so an optional type prefix covers
    both — and is followed by a parameter list. The `^` anchor is what keeps
    an indented *call* to the same function from counting, which matters
    because the binding calls every one of these.
    """
    pat = re.compile(
        rf"^(?:[A-Za-z_][A-Za-z0-9_ *]*\s+)?{re.escape(symbol)}\s*\([^;]*$",
        re.M,
    )
    return len(pat.findall(text))


def _compile(
    path: Path, include_dirs: "list[str]"
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            _CC,
            "-fsyntax-only",
            _STD,
            *(f"-I{d}" for d in include_dirs),
            str(path),
        ],
        capture_output=True,
        text=True,
    )


class TestOneDefinitionPerName:
    """The counting tier — no toolchain, so it runs on every runner."""

    @_BUILDERS
    @pytest.mark.parametrize(
        "name,arg_type,return_type,params,var_out", _CASES
    )
    def test_core_c_defines_the_symbol_once(
        self, tmp_path, build, name, arg_type, return_type, params, var_out
    ):
        root = build(
            tmp_path / "proj", name, arg_type, return_type, params, var_out
        )
        core_c = (root / "native/src/osc/osc_core.c").read_text(
            encoding="utf-8"
        )
        assert _definitions_of(core_c, f"osc_{name}") <= 1, (
            f"osc_core.c defines osc_{name}() more than once — the file jm "
            f"just wrote does not compile"
        )

    @_BUILDERS
    @pytest.mark.parametrize(
        "name,arg_type,return_type,params,var_out", _CASES
    )
    def test_python_member_is_declared_once(
        self, tmp_path, build, name, arg_type, return_type, params, var_out
    ):
        root = build(
            tmp_path / "proj", name, arg_type, return_type, params, var_out
        )
        pyi = (root / "src/proj/osc.pyi").read_text(encoding="utf-8")
        assert pyi.count(f"    def {name}(") <= 1, (
            f"osc.pyi declares {name}() twice — the built-in's stub and the "
            f"declared method's, for one member"
        )
        ext_c = (root / "native/src/osc/osc_ext.c").read_text(encoding="utf-8")
        assert ext_c.count(f'{{"{name}",') <= 1, (
            f"osc_ext.c carries two PyMethodDef rows for {name}"
        )

    @_BUILDERS
    @pytest.mark.parametrize(
        "name,params,decl",
        [
            ("reset", [("start", "uint32_t")], "uint32_t start"),
            ("steps", [("x", "float[]")], "const float *x"),
        ],
        ids=["reset-with-params", "steps-variable-output"],
    )
    def test_an_overriding_method_reaches_the_tree(
        self, tmp_path, build, name, params, decl
    ):
        """Emitting one definition is not enough — it must be the right one.

        The cheap way to satisfy every count above is to always keep the
        built-in and drop the declared method, and the tree then compiles
        while quietly ignoring the signature the manifest asked for. A method
        that adds a parameter or an output shape is not describing the
        built-in; it replaces it, and the replacement is what the author will
        implement.
        """
        root = build(
            tmp_path / "proj",
            name,
            "void",
            "float",
            params,
            name == "steps",
        )
        header = (root / "native/inc/osc/osc_core.h").read_text(
            encoding="utf-8"
        )
        core_c = (root / "native/src/osc/osc_core.c").read_text(
            encoding="utf-8"
        )
        assert decl in header, (
            f"osc_core.h never learned the declared signature of {name}()"
        )
        assert decl in core_c, (
            f"osc_core.c has a definition of osc_{name}(), but not the one "
            f"the manifest declares"
        )

    @pytest.mark.parametrize(
        "name,params,var_out,decl,py_decl",
        [
            (
                "reset",
                [("start", "uint32_t")],
                False,
                "uint32_t start",
                "def reset(self, start: int)",
            ),
            (
                "steps",
                [("x", "float[]")],
                True,
                "const float *x",
                "def steps(",
            ),
        ],
        ids=["reset-with-params", "steps-variable-output"],
    )
    def test_a_second_apply_is_a_no_op(
        self, tmp_path, name, params, var_out, decl, py_decl
    ):
        """Replaying a manifest over its own output must not undo it.

        `jm apply` is idempotent replay, so the second pass asks the same
        collision question of a tree where the answer has already changed
        hands: the `<obj>_reset` in `_core.c` is now the *method's* stub, not
        the built-in's. Read by symbol name alone it looks exactly like the
        thing to defer to, and the method loses its own prototype and its
        binding on every pass after the first.
        """
        root = _via_apply(
            tmp_path / "proj", name, "void", "float", params, var_out
        )
        expected = {
            "native/inc/osc/osc_core.h": decl,
            "native/src/osc/osc_core.c": decl,
            "src/proj/osc.pyi": py_decl,
        }
        before = {
            rel: (root / rel).read_text(encoding="utf-8") for rel in expected
        }
        apply_run(root)
        for rel, wanted in expected.items():
            now = (root / rel).read_text(encoding="utf-8")
            assert wanted in now, (
                f"{rel} lost the declared signature on replay"
            )
            assert now == before[rel], f"{rel} changed on an idempotent replay"

    @pytest.mark.parametrize(
        "name,params,var_out",
        [
            ("reset", [("start", "uint32_t")], False),
            ("steps", [("x", "float[]")], True),
        ],
        ids=["reset-with-params", "steps-variable-output"],
    )
    def test_scaffolding_an_override_is_quiet(
        self, tmp_path, capsys, name, params, var_out
    ):
        """A brand-new object must not be warned about being rewritten.

        There are two ways to give an overriding method the symbol, and only
        one of them is right on a fresh scaffold. jm can write the built-in
        and then retract it — which works, and reports a *sacred header
        rewrite* (gh-632) on an object that did not exist a moment earlier —
        or, on the path that knows the method beforehand, never write it at
        all. The second is the quiet one, and quiet is the point: gh-994 was
        filed by someone scaffolding a new object and trusting the generator,
        which is exactly when an unexplained warning costs the most.
        """
        capsys.readouterr()
        _via_apply(tmp_path / "proj", name, "void", "float", params, var_out)
        out = capsys.readouterr()
        noisy = [
            line
            for line in (out.out + out.err).splitlines()
            if "warning" in line.lower() or "withdrew" in line
        ]
        assert not noisy, (
            "a fresh `jm apply` reported a rewrite of files it had just "
            f"created: {noisy}"
        )

    def test_a_declarative_reset_keeps_the_builtin_body(self, tmp_path):
        """doppler's entry describes the built-in; it must not blank it.

        The trap in "emit only one of the two" is picking the wrong one. A
        bare ``reset`` entry adds no parameters and no output shape, so the
        prototype it implies is the built-in's — and the built-in's body
        actually restores the declared defaults, where the method's stub
        would be an empty ``(void)state;``. Scaffolding that instead would
        satisfy every count above while silently deleting the object's reset
        semantics.
        """
        root = _via_apply(
            tmp_path / "proj", "reset", "void", "void", [], False
        )
        core_c = (root / "native/src/osc/osc_core.c").read_text(
            encoding="utf-8"
        )
        assert "state->gain = 1.0;" in core_c


@_needs_cc
class TestGeneratedTreeCompiles:
    """The compiler tier — the gate that was missing.

    Syntax-only: this asks whether jm emitted a valid translation unit, not
    whether the object links or runs. That is the exact claim gh-994 was
    about (`redefinition of 'bpsk_receiver_reset'`), and it needs no numpy
    runtime, no cmake configure and no wheel.
    """

    @_BUILDERS
    @pytest.mark.parametrize(
        "name,arg_type,return_type,params,var_out", _CASES
    )
    def test_core_c_compiles(
        self, tmp_path, build, name, arg_type, return_type, params, var_out
    ):
        root = build(
            tmp_path / "proj", name, arg_type, return_type, params, var_out
        )
        proc = _compile(
            root / "native/src/osc/osc_core.c", [str(root / "native/inc")]
        )
        assert proc.returncode == 0, proc.stderr

    @_BUILDERS
    @pytest.mark.parametrize(
        "name,arg_type,return_type,params,var_out", _CASES
    )
    def test_ext_c_compiles(
        self, tmp_path, build, name, arg_type, return_type, params, var_out
    ):
        numpy = pytest.importorskip("numpy")
        root = build(
            tmp_path / "proj", name, arg_type, return_type, params, var_out
        )
        proc = _compile(
            root / "native/src/osc/osc_ext.c",
            [
                str(root / "native/inc"),
                sysconfig.get_paths()["include"],
                numpy.get_include(),
            ],
        )
        assert proc.returncode == 0, proc.stderr


class TestHandWrittenBodyIsNeverDeleted:
    """An override needs the built-in's body out of the way — not gone.

    ``_core.c`` is create-only precisely because the author owns what is in
    it. When a declared method replaces a built-in, jm withdraws the built-in
    body to make room for the method's stub — but only while that body is
    still jm's own untouched scaffold. Once it holds real code, jm keeps it,
    skips the method's stub, and says so.
    """

    def test_authored_body_survives_and_is_reported(self, tmp_path, capsys):
        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "osc", None, state_vars=_STATE)
        core_c = root / "native/src/osc/osc_core.c"
        core_c.write_text(
            core_c.read_text(encoding="utf-8").replace(
                "    state->gain = 1.0;", "    state->gain = 42.0; /* mine */"
            ),
            encoding="utf-8",
        )
        method_run(
            root,
            "osc",
            "reset",
            None,
            "void",
            "void",
            False,
            [],
            params=[("start", "uint32_t")],
        )
        text = core_c.read_text(encoding="utf-8")
        assert "state->gain = 42.0; /* mine */" in text
        assert _definitions_of(text, "osc_reset") == 1
        captured = capsys.readouterr()
        assert "osc_reset" in captured.out + captured.err
