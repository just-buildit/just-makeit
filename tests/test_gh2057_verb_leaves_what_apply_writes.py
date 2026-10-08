"""gh-2057: every mutating verb leaves the tree `jm apply` would write.

One bug class, found one instance at a time: a verb changed the project and
left it different from what `jm apply` writes from the same manifest
(gh-1978, gh-1983, gh-1984, gh-1985, gh-2055, gh-1880). Each fix brought its
own narrow sweep. This is the general one.

GATE: for every command `_cli.COMMANDS` classifies MUTATING -- the one
      classification, held complete against `_cli._main`'s dispatch -- run
      each of its cases on every project shape that admits it, with no
      `apply` afterwards, and require

      (a) `jm status --check` exits 0, and
      (b) `jm apply` is a no-op: the tree is byte-identical before and after
          it. This oracle does not go through `status`, which has blind
          spots of its own (gh-1986: a file `apply` would delete reads
          clean).

      `jm new`, the one CREATES command, is held to the same two on each
      flag set it is given, and every shape is a sequence of verbs that must
      itself end in sync.

A verb without a case fails `test_every_mutating_command_has_a_case`. A case
red today is ratcheted in `RATCHET` with its issue and the exact findings it
is red with, so it fails for that cause or not at all (gh-1652); the ratchet
may only shrink.
"""

from __future__ import annotations

import ast
import inspect
import re
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from _jmrun import run_cli
from just_makeit import _cli
from just_makeit import _config as C
from test_cli_dispatch import _dispatched_commands


# ── Shapes ───────────────────────────────────────────────────────────────────


class Shape(NamedTuple):
    """A project a verb runs on: `jm new p <new>`, then *steps*.

    ``has`` is what the shape holds, for a case's ``needs``. Every shape
    names its object ``o``; ``module`` is the module ``o`` lives in, ""
    when it is standalone.
    """

    new: tuple
    steps: tuple
    has: frozenset
    module: str = ""


def _package(module: str, package: str):
    """``[module.X] package`` has no CLI flag (gh-2064): the author's edit."""

    def edit(root: Path) -> None:
        cfg = C.load(root)
        cfg["module"][module]["package"] = package
        C.save(root, cfg)

    edit.__name__ = f"package-{module}-{package}"
    return edit


def _pair(*module: str) -> tuple:
    """A declared scalar element, its WRITER and its READER (gh-1404)."""
    return (
        ("record", "o", "sample", "--type", "float _Complex"),
        ("method", "o", "write", "--arg-type", "sample[]",
         "--return-type", "bool", *module),
        ("method", "o", "wait", "--arg-type", "void", "--return-type",
         "sample", "--borrow", "--param", "n:size_t", *module),
    )  # fmt: skip


def _struct(*module: str) -> tuple:
    """A declared struct element, its writer and its record reader."""
    return (
        ("record", "o", "iq_t", "--field", "i:int16_t", "--field",
         "q:int16_t"),
        ("method", "o", "write", "--arg-type", "iq_t[]",
         "--return-type", "bool", *module),
        ("method", "o", "read", "--borrow", "--param", "n:size_t",
         "--return-type", "float _Complex", "--record-dtype", "iq_t",
         *module),
    )  # fmt: skip


def _enum(name: str, *values: str):
    """An ``[[enum]]`` table, which only the manifest declares."""

    def edit(root: Path) -> None:
        cfg = C.load(root)
        cfg.setdefault("enum", []).append({"name": name, "values": values})
        C.save(root, cfg)

    edit.__name__ = f"enum-{name}"
    return edit


def _impl_source(root: Path) -> None:
    """A C file `--impl` lifts a body from (``impl.c::lifted``)."""
    (root / "impl.c").write_text(
        "double\nlifted(double x)\n{\n    return 2.0 * x;\n}\n",
        encoding="utf-8",
    )


def _members(*module: str) -> tuple:
    """One of every member `jm remove` takes out of an object, and the
    state a backed property reads (``x``, the buffer ``buf`` and its
    length ``n``)."""
    return (
        ("method", "o", "m", *module),
        ("property", "o", "lvl", "--type", "double", *module),
        ("warning", "o", "--condition", "gain", "--message", "hot",
         *module),
        ("error", "o", "--category", "ValueError", "--message", "bad",
         *module),
        ("add", "--object", "o", "--state", "x:double:0", "--state",
         "buf:float[8]", "--state", "n:size_t:8", "--force"),
        _enum("mode", "off", "on"),
    )  # fmt: skip


_M = ("--module", "mod")
_MOD = (("module", "mod"), ("object", "o", *_M))
_O = frozenset({"o", "standalone"})
_OM = frozenset({"o", "module"})

SHAPES: "dict[str, Shape]" = {
    "empty": Shape((), (), frozenset()),
    "standalone": Shape((), (("object", "o"), _impl_source), _O),
    "module": Shape((), (*_MOD, _impl_source), _OM, "mod"),
    # gh-2054: the class is importable from the package, not the module id.
    "package": Shape(
        (),
        (
            ("module", "mod"),
            _package("mod", "other"),
            ("object", "o", *_M),
            _impl_source,
        ),
        _OM | {"package"},
        "mod",
    ),  # fmt: skip
    "dotted": Shape(
        (),
        (("module", "dsp.filt"), ("object", "o", "--module", "dsp.filt")),
        _OM | {"dotted"},
        "dsp.filt",
    ),
    "pair": Shape((), (("object", "o"), *_pair()), _O | {"pair"}),
    "pair-in-module": Shape((), (*_MOD, *_pair(*_M)), _OM | {"pair"}, "mod"),
    "struct": Shape((), (("object", "o"), *_struct()), _O | {"struct"}),
    "struct-in-module": Shape(
        (), (*_MOD, *_struct(*_M)), _OM | {"struct"}, "mod"
    ),
    "members": Shape(
        (), (("object", "o"), *_members()), _O | {"members", "enum"}
    ),
    "members-in-module": Shape(
        (), (*_MOD, *_members(*_M)), _OM | {"members", "enum"}, "mod"
    ),
    "function": Shape(
        (),
        (
            *_MOD,
            (
                "function",
                "f",
                *_M,
                "--param",
                "x:double",
                "--return-type",
                "double",
            ),
            _enum("mode", "off", "on"),
        ),
        _OM | {"function", "enum"},
        "mod",
    ),  # fmt: skip
    "view": Shape(
        (),
        (*_MOD, ("view", "o", "V", *_M, "--create-fn", "o_create_v")),
        _OM | {"view"},
        "mod",
    ),
    # Every app target, over an object and a function, so a verb that
    # changes either meets the apps that name it.
    "apps": Shape(
        (),
        (
            *_MOD,
            (
                "function",
                "f",
                *_M,
                "--param",
                "x:double",
                "--return-type",
                "double",
            ),
            ("app", "--object", "o", *_M, "--target", "c"),
            ("app", "--object", "o", *_M, "--target", "console"),
            ("app", "--function", "f", *_M, "--target", "pep723"),
        ),
        _OM | {"function", "apps"},
        "mod",
    ),  # fmt: skip
    # gh-1985: every name added sorts before the ones already there.
    "reverse": Shape(
        (),
        (
            ("object", "q"),
            ("object", "o"),
            ("module", "r"),
            ("module", "n"),
            ("object", "x", "--module", "r"),
        ),
        _O,
    ),  # fmt: skip
    "central": Shape(
        ("--no-fragments",),
        (("object", "o"), ("module", "mod"), ("object", "k", *_M)),
        _O | {"central"},
    ),
    "make": Shape(("--build-system", "make"), (("object", "o"),), _O),
    "no-c-prefix": Shape(("--no-c-prefix",), (("object", "o"),), _O),
    # gh-2062: a vendored C dependency, and nothing else yet.
    "c-dep": Shape(("--c-dep", "vend"), (), frozenset({"c-dep"})),
}


# ── Cases ────────────────────────────────────────────────────────────────────


class Case(NamedTuple):
    """One run of a verb: argv, what the shape must hold, which shapes.

    In ``argv``, ``{M}`` is ``--module <id>`` when ``o`` lives in a module
    and nothing when it is standalone; ``{mod}`` is that module's id.
    ``only`` restricts the case to the named shapes; by default it runs on
    every shape that holds ``needs``.
    """

    argv: tuple
    needs: frozenset = frozenset({"o"})
    only: "frozenset | None" = None


def _case(*argv: str, needs=("o",), only=None) -> Case:
    return Case(argv, frozenset(needs), frozenset(only) if only else None)


#: The placements a flag variant runs on: what decides where its files go.
BASE = ("standalone", "module", "package")

_FN = ("--param", "x:double", "--return-type", "double")

#: verb -> case name -> Case. Held to `_cli.COMMANDS` by
#: `test_every_mutating_command_has_a_case`: the classification is the list.
CASES: "dict[str, dict[str, Case]]" = {
    "object": {
        # `a` sorts before every name a shape holds (gh-1985).
        "standalone": _case("object", "a", needs=()),
        "in-a-module": _case("object", "a", "{M}", needs=("module",)),
        "generator": _case("object", "a", "--preset", "generator",
                           only=BASE),
        "consumer": _case("object", "a", "--preset", "consumer", only=BASE),
        "reader": _case("object", "a", "--preset", "reader", only=BASE),
        "blockwise": _case("object", "a", "--preset", "blockwise",
                           only=BASE),
        "state-and-init": _case(
            "object", "a", "--state", "g:double:1", "--state",
            "buf:float[8]", "--init-param", "rate:double", only=BASE),
        "perf-mutable": _case("object", "a", "--perf", "--mutable",
                              only=BASE),
        "streamable": _case("object", "a", "--streamable", "--stream-block",
                            "256", only=BASE),
        "no-state": _case("object", "a", "--no-state", only=BASE),
        "no-step": _case("object", "a", "--no-step", only=BASE),
        "opaque-no-reset": _case("object", "a", "--opaque-state",
                                 "--no-step", "--no-reset", only=BASE),
        "serializable": _case("object", "a", "--serializable", only=BASE),
        "delegates": _case("object", "a", "--step-delegates-to-steps",
                           only=BASE),
        "variable-output": _case("object", "a", "--variable-output",
                                 "--max-out", "8", only=BASE),
        "class-and-create-fn": _case(
            "object", "a", "--class-name", "Renamed", "--create-fn",
            "a_create_custom", only=BASE),
        "explicit-types": _case("object", "a", "--arg-type", "double",
                                "--return-type", "float", "--param",
                                "g:double:2", only=BASE),
        "array-arg": _case("object", "a", "--array-arg", "taps:float32",
                           only=BASE),
        "async-stream": _case("object", "a", "--arg-type", "void",
                              "--async-stream", only=BASE),
        "header-only": _case("object", "a", "--header-only", only=BASE),
        "multi-output": _case("object", "a", "--variable-output",
                              "--multi-output", "uint8_t",
                              "--method-name", "execute", only=BASE),
        "extra-include-dirs": _case("object", "a", "--extra-include-dirs",
                                    "${FOO_INCLUDE_DIR}", only=BASE),
        "impl": _case("object", "a", "--arg-type", "double",
                      "--return-type", "double", "--impl",
                      "impl.c::lifted", "--replace", "2.0::3.0",
                      only=BASE),
    },
    "module": {
        "sorts-first": _case("module", "a", needs=()),
        "sorts-last": _case("module", "zz", needs=()),
        "dotted": _case("module", "dsp.extra", needs=(), only=BASE),
        "functions-in-core": _case("module", "a", "--functions-in-core",
                                   "--doc", "A module.", needs=(),
                                   only=BASE),
        "extras": _case("module", "a", "--extra-include-dirs",
                        "${FOO_INCLUDE_DIR}", "--extra-link-libs", "foo",
                        "--extra-types", "Foo", needs=(), only=BASE),
    },
    "method": {
        "plain": _case("method", "o", "m2", "{M}"),
        "params": _case("method", "o", "m2", "{M}", "--param", "x:double",
                        "--extra-arg", "g:double=1.0", "--return-type",
                        "double"),
        "array-param": _case("method", "o", "m2", "{M}", "--param",
                             "buf:float[]", "--return-type", "size_t",
                             only=BASE),
        "out-param": _case("method", "o", "m2", "{M}", "--param",
                           "x:float[]", "--out-param", "y:float[]",
                           "--return-type", "void", only=BASE),
        "variable-output": _case(
            "method", "o", "m2", "{M}", "--arg-type", "float _Complex",
            "--return-type", "float _Complex", "--variable-output"),
        "variable-output-generator": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "double", "--variable-output",
            "--count-default", "16", "--count-name", "n", only=BASE),
        "capacity-nogil": _case(
            "method", "o", "m2", "{M}", "--param", "x:float _Complex[]",
            "--variable-output", "--pass-capacity", "--nogil",
            "--exact-max-out", "--max-out", "8", only=BASE),
        "multi-output": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "uint32_t", "--variable-output",
            "--multi-output", "uint8_t", only=BASE),
        "error-negative-count": _case(
            "method", "o", "m2", "{M}", "--param", "x:float[]",
            "--return-type", "float", "--variable-output", "--count-type",
            "int64_t", "--error-negative", "--error", "ValueError",
            "--error-message", "bad", only=BASE),
        "error-sentinel": _case(
            "method", "o", "m2", "{M}", "--param", "x:float[]",
            "--return-type", "float", "--variable-output",
            "--error-sentinel", "SIZE_MAX", only=BASE),
        "error-on-empty": _case(
            "method", "o", "m2", "{M}", "--param", "x:float[]",
            "--return-type", "float", "--variable-output",
            "--error-on-empty", only=BASE),
        "batch": _case("method", "o", "m2", "{M}", "--arg-type", "float",
                       "--return-type", "float", "--batch", only=BASE),
        "out-type": _case("method", "o", "m2", "{M}", "--param",
                          "x:int8_t[]", "--out-type", "float _Complex",
                          "--out-divisor", "2", "--return-type", "void",
                          only=BASE),
        "borrow": _case("method", "o", "m2", "{M}", "--arg-type", "void",
                        "--return-type", "float", "--borrow", "--param",
                        "n:size_t"),
        "borrow-writeable": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "float", "--borrow", "--param", "n:size_t",
            "--param", "k:size_t", "--borrow-count", "n",
            "--borrow-writeable", "--none-on-empty", only=BASE),
        "borrow-status": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "float", "--borrow", "--param", "n:size_t",
            "--status-fn", "o_m2_status", "--status-error",
            "O_EOF:EOFError:done", only=BASE),
        "releases": _case("method", "o", "consume", "{M}", "--param",
                          "k:size_t", "--return-type", "void",
                          "--releases", "wait", needs=("o", "pair")),
        "release-count": _case(
            "method", "o", "consume", "{M}", "--param", "j:int",
            "--param", "k:size_t", "--return-type", "void", "--releases",
            "wait", "--release-count", "k", needs=("o", "pair")),
        "single": _case(
            "method", "o", "m2", "{M}", "--return-type", "stats_t",
            "--single", "--result-field", "n:uint64_t", "--result-field",
            "mean:double:the mean", "--record-name", "Stats",
            "--record-doc", "Count and mean.", "--record-module",
            "p.records", only=BASE),
        "result-list": _case(
            "method", "o", "m2", "{M}", "--return-type", "peak_t",
            "--result-field", "index:size_t", "--result-field",
            "value:double", only=BASE),
        "record-dtype": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "double", "--variable-output",
            "--record-dtype", "rec_t", "--result-field", "t:uint64_t",
            "--result-field", "v:double", only=BASE),
        "speaks-the-element": _case(
            "method", "o", "w2", "{M}", "--arg-type", "sample[]",
            "--return-type", "bool", needs=("o", "pair")),
        "reads-the-struct": _case(
            "method", "o", "r2", "{M}", "--borrow", "--param", "n:size_t",
            "--return-type", "float _Complex", "--record-dtype", "iq_t",
            needs=("o", "struct")),
        "status-return": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "int", "--status-return", "--error",
            "RuntimeError", "--error-message", "failed", only=BASE),
        "error-negative": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "int32_t", "--error-negative", only=BASE),
        "fn": _case("method", "o", "m2", "{M}", "--arg-type", "double",
                    "--return-type", "double", "--fn", "o_custom_m2",
                    only=BASE),
        "varargs": _case("method", "o", "m2", "{M}", "--varargs",
                         only=BASE),
        "manual-stub": _case("method", "o", "m2", "{M}", "--manual-stub",
                             only=BASE),
        "impl": _case("method", "o", "m2", "{M}", "--arg-type", "double",
                      "--return-type", "double", "--impl",
                      "impl.c::lifted", "--replace", "2.0::3.0",
                      only=BASE),
        "strict": _case("method", "o", "m2", "{M}", "--param",
                        "x:float[]", "--strict", "--return-type", "size_t",
                        only=BASE),
        "py-return-type": _case(
            "method", "o", "m2", "{M}", "--arg-type", "void",
            "--return-type", "int", "--py-return-type", "bool",
            "--no-bench", "--doc", "A method.", only=BASE),
        "on-a-view": _case("method", "o", "vm", "{M}", "--view", "V",
                           "--arg-type", "double", "--return-type",
                           "double", needs=("o", "view")),
    },
    "view": {
        "plain": _case("view", "o", "W", "{M}", "--create-fn",
                       "o_create_w", needs=("o", "module")),
        "flags": _case("view", "o", "W", "{M}", "--create-fn",
                       "o_create_w", "--init-param", "rate:double",
                       "--doc", "A view.", needs=("o", "module")),
        "excludes": _case("view", "o", "W", "{M}", "--create-fn",
                          "o_create_w", "--exclude-property", "lvl",
                          "--exclude-method", "m",
                          needs=("o", "module", "members")),
    },
    "remove": {
        "object": _case("remove", "object", "o", "--force"),
        "module": _case("remove", "module", "{mod}", "--force",
                        needs=("o", "module")),
        "method": _case("remove", "method", "m", "--object", "o",
                        "--force", needs=("o", "members")),
        "property": _case("remove", "property", "lvl", "--object", "o",
                          "--force", needs=("o", "members")),
        "warning": _case("remove", "warning", "gain", "--object", "o",
                         "--force", needs=("o", "members")),
        "error": _case("remove", "error", "o", "--object", "o", "--force",
                       needs=("o", "members")),
        "state": _case("remove", "state", "x", "--object", "o", "--force",
                       needs=("o", "members")),
        "function": _case("remove", "function", "f", "--module", "{mod}",
                          "--force", needs=("o", "function")),
        "a-pair-reader": _case("remove", "method", "wait", "--object", "o",
                               "--force", needs=("o", "pair")),
        "a-pair-writer": _case("remove", "method", "write", "--object",
                               "o", "--force", needs=("o", "pair")),
        "a-struct-reader": _case("remove", "method", "read", "--object",
                                 "o", "--force", needs=("o", "struct")),
    },
    "property": {
        "plain": _case("property", "o", "lvl2", "--type", "double", "{M}"),
        "writable": _case("property", "o", "lvl2", "--type", "double",
                          "--writable", "--doc", "A level.", "{M}",
                          only=BASE),
        "field": _case("property", "o", "x", "--type", "double",
                       "--field", "{M}", needs=("o", "members")),
        "expr": _case("property", "o", "twice", "--type", "double",
                      "--expr", "state->x * 2.0", "{M}",
                      needs=("o", "members")),
        "capsule": _case("property", "o", "_capsule", "--type", "capsule",
                         "--capsule", "p.o", "{M}", only=BASE),
        "on-a-view": _case("property", "o", "vp", "--type", "double",
                           "--view", "V", "{M}", needs=("o", "view")),
        "buffer": _case("property", "o", "samples", "--type", "float[]",
                        "--buf-field", "buf", "--len-field", "n",
                        "--valid-field", "n", "{M}",
                        needs=("o", "members")),
        "enum": _case("property", "o", "mode", "--type", "int", "--enum",
                      "mode", "--writable", "{M}",
                      needs=("o", "members", "enum")),
        "container": _case("property", "o", "tags", "--type", "dict",
                           "--value-type", "object", "--count-fn",
                           "o_ntags", "--key-fn", "o_tag_key",
                           "--value-fn", "o_tag_val", "{M}", only=BASE),
        "list": _case("property", "o", "gains", "--type", "list",
                      "--value-type", "double", "{M}", only=BASE),
        "capsule-type": _case("property", "o", "_capsule", "--type",
                              "capsule", "--capsule", "p.o",
                              "--capsule-type", "p_o_state_t", "{M}",
                              only=BASE),
    },
    "warning": {
        "plain": _case("warning", "o", "--condition", "g2", "--message",
                       "hot", "{M}"),
        "category": _case("warning", "o", "--condition", "g2", "--message",
                          "hot", "--category", "RuntimeWarning",
                          "--stacklevel", "2", "{M}", only=BASE),
        "after-init": _case("warning", "o", "--condition", "g2",
                             "--message", "hot", "--after", "__init__",
                             "{M}", only=BASE),
        "on-a-view": _case("warning", "o", "--condition", "g2",
                           "--message", "hot", "--view", "V", "{M}",
                           needs=("o", "view")),
    },
    "error": {
        "plain": _case("error", "o", "--category", "ValueError",
                       "--message", "bad", "{M}", only=BASE),
        "on-a-view": _case("error", "o", "--category", "ValueError",
                           "--message", "bad", "--view", "V", "{M}",
                           needs=("o", "view")),
    },
    "function": {
        "plain": _case("function", "g", "--module", "{mod}", *_FN,
                       needs=("o", "module")),
        # `a` sorts before every function a shape holds.
        "sorts-first": _case("function", "a", "--module", "{mod}", *_FN,
                             needs=("function",)),
        "variable-output": _case(
            "function", "g", "--module", "{mod}", "--param", "x:float[]",
            "--variable-output", "--out-type", "float", "--return-type",
            "size_t", "--out-size", "x_len", needs=("o", "module"),
            only=BASE),
        "out-param": _case(
            "function", "g", "--module", "{mod}", "--param", "x:float[]",
            "--out-param", "y:float[]", "--return-type", "void",
            needs=("o", "module"), only=BASE),
        "check-return": _case(
            "function", "g", "--module", "{mod}", "--param", "x:double",
            "--return-type", "int", "--check-return", "--doc", "A check.",
            needs=("o", "module"), only=BASE),
        "inline": _case("function", "g", "--module", "{mod}", *_FN,
                        "--inline", needs=("o", "module"), only=BASE),
        "why-status": _case(
            "function", "g", "--module", "{mod}", "--param", "x:double",
            "--return-type", "int", "--check-return", "--why",
            "--status-error", "P_BAD:ValueError:bad",
            needs=("o", "module"), only=BASE),
        "result-field": _case(
            "function", "g", "--module", "{mod}", "--param", "x:double",
            "--return-type", "peak_t", "--result-field", "index:size_t",
            "--result-field", "value:double", needs=("o", "module"),
            only=BASE),
        "path-and-enum": _case(
            "function", "g", "--module", "{mod}", "--param",
            "fname:path", "--param", "m:enum:mode", "--return-type", "int",
            needs=("function", "enum")),
        "impl": _case("function", "g", "--module", "{mod}", *_FN,
                      "--impl", "impl.c::lifted", "--replace", "2.0::3.0",
                      needs=("o", "module"), only=BASE),
    },
    "add": {
        "state": _case("add", "--object", "o", "--state", "y:double:0",
                       "--force"),
        "array-state": _case("add", "--object", "o", "--param",
                             "ring:float[16]", "--force", only=BASE),
    },
    "record": {
        "scalar": _case("record", "o", "el2", "--type", "float"),
        "struct": _case("record", "o", "rec2", "--field", "a:float",
                        "--field", "b:int32_t", only=BASE),
        # gh-2055: a column change re-renders the members speaking it.
        "a-column-change": _case(
            "record", "o", "iq_t", "--field", "i:int16_t", "--field",
            "q:int16_t", "--field", "t:uint32_t", needs=("o", "struct")),
        "same-type-redeclared": _case(
            "record", "o", "sample", "--type", "float _Complex", "--doc",
            "IQ.", needs=("o", "pair")),
    },
    "app": {
        "c": _case("app", "--object", "o", "{M}", "--target", "c"),
        "pep723": _case("app", "--object", "o", "{M}", "--target",
                        "pep723", only=BASE),
        "console": _case("app", "--object", "o", "{M}", "--target",
                         "console", "--name", "o-cli", "--flag",
                         "gain:double:1.0:the gain", "--command",
                         "run:run it", only=BASE),
        "c-argv": _case("app", "--object", "o", "{M}", "--target", "c",
                        "--argc-argv", only=BASE),
        "function": _case("app", "--function", "f", "--module", "{mod}",
                          "--target", "pep723", needs=("function",)),
    },
    "perf": {"perf": _case("perf")},
    "upgrade": {"upgrade": _case("upgrade", needs=())},
    "apply": {
        "apply": _case("apply"),
        "only": _case("apply", "--only=o"),
    },
    "regenerate": {
        "regenerate": _case("regenerate", "o", "--force"),
        "discard": _case("regenerate", "o", "--force", "--discard",
                         needs=("o", "module")),
    },
    "split-objects": {
        "split-objects": _case("split-objects", needs=("central",)),
    },
    "migrate-to-fragments": {
        "migrate": _case("migrate-to-fragments", needs=("central",)),
    },
    "adopt": {
        "all": _case("adopt", "--all", needs=("module",)),
        "a-module": _case("adopt", "--module", "{mod}",
                          "--accept-additions", needs=("module",)),
        "check": _case("adopt", "--check", "--all", needs=("module",)),
        "packaging": _case("adopt", "--packaging", needs=()),
    },
    "bind": {
        "bind": _case("bind", "o", needs=("o", "standalone")),
        # Read-only, and its exit status is its verdict: run where `bind`
        # renders what `apply` does, so a non-zero one is a regression.
        "check": _case("bind", "o", "--check", only=("standalone",)),
    },
    "config": {"version": _case("config", "version", "0.2.0", needs=())},
    "ci": {
        "github": _case("ci", needs=()),
        "woodpecker": _case("ci", "--provider", "woodpecker", "--force",
                            needs=(), only=BASE),
    },
}  # fmt: skip

#: `jm new` flag sets: the CREATES command, held to the same two oracles.
NEW_CASES: "dict[str, tuple]" = {
    "bare": (),
    "objects": ("--object", "q", "--object", "o"),
    "a-module": ("--module", "m"),
    "central": ("--no-fragments",),
    "c-dep": ("--c-dep", "vend"),
    "make": ("--build-system", "make"),
    "no-c-prefix": ("--no-c-prefix",),
    "c-prefix": ("--c-prefix", "zz"),
    "perf-pytest": ("--perf", "--pytest", "--pytest-benchmark"),
    "deps": ("--find-package", "Foo", "--pkg-module", "zlib"),
    "object-flags": ("--object", "o", "--state", "g:double:1",
                     "--param", "h:int:2", "--arg-type", "double",
                     "--return-type", "float", "--mutable"),
    "no-state-step-reset": ("--object", "o", "--no-state", "--no-step",
                            "--no-reset"),
    "basic-fragments": ("--basic", "--fragments", "--object", "o"),
    "windows-c-style": ("--windows", "--c-style", "clang-format",
                        "--object", "o"),
}  # fmt: skip


#: (verb, case, shape) -> (issue, the findings it is red with). Only ever
#: shrinks; an entry is checked against the exact findings, so it fails for
#: its issue's cause or not at all (gh-1652).
RATCHET: "dict[tuple[str, str, str], tuple[str, frozenset]]" = {}


# ── Running a case ───────────────────────────────────────────────────────────


def _expand(argv: tuple, shape: Shape) -> "list[str]":
    out: "list[str]" = []
    for tok in argv:
        if tok == "{M}":
            out += ["--module", shape.module] if shape.module else []
        else:
            out.append(tok.replace("{mod}", shape.module))
    return out


def _admits(case: Case, name: str) -> bool:
    shape = SHAPES[name]
    if case.only is not None and name not in case.only:
        return False
    return case.needs <= shape.has


def _params():
    for verb, cases in CASES.items():
        for cname, case in cases.items():
            for sname in SHAPES:
                if _admits(case, sname):
                    yield pytest.param(
                        verb, cname, sname, id=f"{verb}-{cname}@{sname}"
                    )


_EXCLUDED = {"build", "__pycache__", ".pytest_cache"}


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not _EXCLUDED & set(p.relative_to(root).parts)
    }


_SECTION = re.compile(r"^([A-Z][A-Z -]*?) \(\d+\)")
_FINDING = re.compile(r"^  (\S) (\S+?):?(?:\s|$)")


def _status_lines(out: str) -> "set[str]":
    """``SECTION mark path`` for each path `status` lists under a heading."""
    found, section = set(), ""
    for line in out.splitlines():
        if head := _SECTION.match(line):
            section = head.group(1)
        elif (item := _FINDING.match(line)) and section:
            found.add(f"status:{section} {item.group(1)} {item.group(2)}")
    return found


def findings(root: Path) -> "frozenset[str]":
    """What the two oracles report, as ``oracle:... path`` strings.

    (a) every path `status --check` lists when it exits non-zero, with its
    section; the bare exit code if it lists none, so a red is never empty.
    (b) every path `apply` creates (+), changes (~) or deletes (-).
    """
    out = set()
    s = run_cli("status", "--check", cwd=root)
    if s.returncode:
        out |= _status_lines(s.stdout) or {f"status:exit {s.returncode}"}
    before = _tree(root)
    a = run_cli("apply", cwd=root)
    after = _tree(root)
    if a.returncode and _declares_nothing(root):
        # `apply` refuses a manifest that declares nothing: it has nothing
        # to materialize, so (b) cannot speak there and (a) is the oracle.
        assert after == before, "a refused `apply` wrote to the tree"
        return frozenset(out)
    if a.returncode:
        # A manifest `apply` refuses is a tree no `apply` can reach.
        (last, *_) = reversed(a.stderr.strip().splitlines() or ["?"])
        out.add(f"apply:refused {last}")
    for rel in set(before) | set(after):
        if before.get(rel) != after.get(rel):
            mark = (
                "+" if rel not in before else "-" if rel not in after else "~"
            )
            out.add(f"apply:{mark} {rel}")
    return frozenset(out)


def _declares_nothing(root: Path) -> bool:
    cfg = C.load(root)
    return not C.components(cfg) and not C.modules(cfg)


def _ok(root: Path, argv) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (list(argv), (r.stdout + r.stderr)[-3000:])


def _build(base: Path, shape: Shape) -> Path:
    _ok(base, ("new", "p", *shape.new))
    root = base / "p"
    for step in shape.steps:
        if callable(step):
            step(root)
        else:
            _ok(root, step)
    return root


_BUILT: "dict[str, Path]" = {}


@pytest.fixture
def shape_copy(tmp_path_factory, tmp_path):
    """A fresh copy of a shape, built once per worker."""

    def copy(name: str) -> Path:
        if name not in _BUILT:
            base = tmp_path_factory.mktemp(f"shape-{name}")
            _BUILT[name] = _build(base, SHAPES[name])
        dest = tmp_path / "p"
        shutil.copytree(_BUILT[name], dest, symlinks=True)
        return dest

    return copy


# ── The classification ───────────────────────────────────────────────────────


def test_every_dispatched_command_is_classified():
    """`_cli.COMMANDS` is the one answer to "what does this command do to
    the tree", so it covers exactly what `_main` dispatches."""
    assert set(_cli.COMMANDS) == _dispatched_commands()
    kinds = {_cli.CREATES, _cli.MUTATING, _cli.READ_ONLY, _cli.BUILD}
    assert set(_cli.COMMANDS.values()) <= kinds


def test_the_format_pass_reads_the_classification():
    """The post-command pass sweeps exactly the commands that change the
    project in cwd -- derived, so it cannot lose one again (it had lost
    `record` and `app`)."""
    assert _cli._C_EMITTING_COMMANDS == {
        c for c, k in _cli.COMMANDS.items() if k == _cli.MUTATING
    }


def test_every_mutating_command_has_a_case():
    mutating = {c for c, k in _cli.COMMANDS.items() if k == _cli.MUTATING}
    assert set(CASES) == mutating, (
        f"mutating commands without a case: {sorted(mutating - set(CASES))};"
        f" cases for no mutating command: {sorted(set(CASES) - mutating)}"
    )
    creates = {c for c, k in _cli.COMMANDS.items() if k == _cli.CREATES}
    assert creates == {"new"} and NEW_CASES


def test_every_case_runs_somewhere():
    """A case no shape admits would read as coverage and run nothing."""
    idle = [
        f"{verb}-{cname}"
        for verb, cases in CASES.items()
        for cname, case in cases.items()
        if not any(_admits(case, s) for s in SHAPES)
    ]
    assert not idle, idle


_FLAG = re.compile(r"--[a-z][a-z0-9-]*")


def parser_flags(cmd: str) -> "set[str]":
    """Every ``--flag`` *cmd*'s parser names, read from the source.

    The ``cmd == "<cmd>"`` branch of `_cli._main`, and the ``_cli_*``
    module it hands its argv to. A string constant that is exactly a flag
    is one a token is compared against, or one a preset expands into; a
    message that mentions a flag is a longer string, so it is not read.
    """
    tree = ast.parse(inspect.getsource(_cli._main))
    (body,) = [
        node.body
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "cmd"
        and [getattr(c, "value", None) for c in node.test.comparators] == [cmd]
    ]
    nodes = [n for top in body for n in ast.walk(top)]
    for imp in [n for n in nodes if isinstance(n, ast.ImportFrom)]:
        if (imp.module or "").startswith("_cli_"):
            path = Path(_cli.__file__).with_name(f"{imp.module}.py")
            nodes += ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    return {
        n.value
        for n in nodes
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and _FLAG.fullmatch(n.value)
    }


def _flags_cased(cmd: str) -> "set[str]":
    """The flags *cmd*'s cases pass (``{M}`` is ``--module``)."""
    if cmd == "new":
        argvs = [*NEW_CASES.values(), *(s.new for s in SHAPES.values())]
    else:
        argvs = [c.argv for c in CASES[cmd].values()]
    return {
        "--module" if tok == "{M}" else tok.split("=")[0]
        for argv in argvs
        for tok in argv
        if tok == "{M}" or _FLAG.fullmatch(tok.split("=")[0])
    }


#: (command, flag) -> why no case passes it. May only shrink.
UNCOVERED_FLAGS: "dict[tuple[str, str], str]" = {
    ("adopt", "--help"): "prints the usage and exits; it writes nothing.",
}


def test_every_flag_has_a_case():
    """A flag no case passes is a render path nothing here runs: the
    issue's "a new verb or flag is covered by nothing". The flags are the
    parsers' own, so a new one fails here until a case passes it."""
    writers = [
        c
        for c, k in _cli.COMMANDS.items()
        if k in (_cli.MUTATING, _cli.CREATES)
    ]
    missing, stale = [], []
    for cmd in writers:
        flags = parser_flags(cmd)
        cased = _flags_cased(cmd)
        missing += [
            (cmd, f)
            for f in sorted(flags - cased)
            if (cmd, f) not in UNCOVERED_FLAGS
        ]
    for (cmd, f), _why in UNCOVERED_FLAGS.items():
        if f not in parser_flags(cmd) or f in _flags_cased(cmd):
            stale.append((cmd, f))
    assert not missing, f"flags no case passes: {missing}"
    assert not stale, f"UNCOVERED_FLAGS entries to delete: {stale}"


def test_every_ratchet_entry_names_a_case_and_an_issue():
    ids = {(v, c, s) for v, cs in CASES.items() for c in cs for s in SHAPES}
    ids |= {("new", c, "") for c in NEW_CASES}
    ids |= {("shape", s, "") for s in SHAPES}
    for key, (issue, found) in RATCHET.items():
        assert key in ids, key
        assert re.fullmatch(r"gh-\d+", issue), (key, issue)
        assert found, key


def _check(key: "tuple[str, str, str]", found: "frozenset[str]") -> None:
    issue, expected = RATCHET.get(key, ("", frozenset()))
    if issue and not found:
        pytest.fail(
            f"{key} is in sync now: delete its RATCHET entry ({issue})."
        )
    assert found == expected, (
        f"{key}: the tree differs from what `jm apply` writes"
        + (f" (ratcheted for {issue})" if issue else "")
        + ":\n  "
        + "\n  ".join(sorted(found ^ expected))
    )


# ── The gate ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_the_shape_leaves_what_apply_writes(tmp_path, name):
    """Every shape is a sequence of verbs: it ends in sync, or a verb
    below would be blamed for what the shape left."""
    root = _build(tmp_path, SHAPES[name])
    _check(("shape", name, ""), findings(root))


@pytest.mark.parametrize("name", sorted(NEW_CASES))
def test_new_leaves_what_apply_writes(tmp_path, name):
    _ok(tmp_path, ("new", "p", *NEW_CASES[name]))
    _check(("new", name, ""), findings(tmp_path / "p"))


@pytest.mark.parametrize("verb, cname, sname", list(_params()))
def test_the_verb_leaves_what_apply_writes(shape_copy, verb, cname, sname):
    case = CASES[verb][cname]
    shape = SHAPES[sname]
    root = shape_copy(sname)
    _ok(root, _expand(case.argv, shape))
    _check((verb, cname, sname), findings(root))
