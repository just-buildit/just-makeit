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
      itself end in sync. On the shapes and after `jm remove`, a third:

      (c) the files `apply` renders whole are the ones a fresh `jm new`,
          given the same manifest and an `apply`, renders: the incremental
          path against the from-scratch one, and the only oracle here that
          sees a file nothing derives any more.

      A case whose verb must REFUSE on its shapes (`Case.refuses`) is held
      to that first: it exits non-zero, prints one ``error:`` line naming
      the route it offers instead, and leaves the tree byte-identical. Then
      to (a) and (b), on the shape's own tree.

      And every case is run once more with a failure injected where it
      would have succeeded -- after every write it makes -- and held to

      (d) exit 1, one ``error:`` line, and the tree byte-identical: every
          file, every directory, and the author's own edit to each
          ``_core.c`` (gh-1867, gh-2040).

Registration-free where the source can say it: the commands are the
dispatch's (`test_every_dispatched_command_is_classified`), a mutating
command without a case fails `test_every_mutating_command_has_a_case`, and
a flag its parser accepts that no case passes fails
`test_every_flag_has_a_case`. The cases themselves -- a representative
argv per render path -- are the one hand list, held to those three.

Every shape, every `jm new` flag set and every case runs in every `make
test`: each case on its home shapes (`_home`), plus every ratcheted run.
The whole cross product -- every case on every shape that admits it --
runs where ``JM_VERB_GATE_FULL=1`` (`FULL_ENV`): one CI leg, held to it by
`test_the_whole_matrix_has_an_execution_home`. On every leg it added 5-8
minutes and timed Coverage out (gh-2057).

A run red today is ratcheted in `RATCHET` under the issue each of its
findings belongs to, and must report exactly those: so it fails for that
cause or not at all (gh-1652), a fix must delete its part of the entry, and
the ratchet only shrinks. `RATCHET` is read from one file per issue,
``tests/gh2057_ratchet/gh-<n>.toml``, so a fix edits only its own issue's
file and two fixes never conflict there (gh-2108).
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from _jmrun import run_cli
from _scratchgit import Repo
from just_makeit import _cli
from just_makeit import _cli_remove
from just_makeit import _config as C
from just_makeit import _createonly as CO
from just_makeit._upgrade import MIGRATIONS, AppRows, _manifest_fragments
from test_cli_dispatch import _dispatched_commands
from test_gh1648_exports_run_on_windows import CI_YML, _job_block
from test_own_ci_matrix import _axes


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
    """``[module.X] package`` set by hand AFTER `jm module` (gh-2081): the
    one route left to an existing module, since `jm module --package`
    declares it up front (gh-2064)."""

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


def _app_table(root: Path) -> None:
    """The manifest as the schema before `AppRows` spelled it: its one app
    a single ``[app]`` table (gh-2074). That schema is read off the
    migration table, not restated."""
    (schema,) = [
        n for n, steps in MIGRATIONS.items() if AppRows() in steps
    ]  # fmt: skip
    path = root / C.FILENAME
    text = path.read_text(encoding="utf-8")
    text, rows = re.subn(r"(?m)^\[\[app\]\]$", "[app]", text)
    text, schemas = re.subn(
        r'(?m)^schema = "\d+"$', f'schema = "{schema}"', text
    )
    assert (rows, schemas) == (1, 1), (rows, schemas)
    path.write_text(text, encoding="utf-8")


def _impl_source(root: Path) -> None:
    """A C file `--impl` lifts a body from (``impl.c::lifted``)."""
    (root / "impl.c").write_text(
        "double\nlifted(double x)\n{\n    return 2.0 * x;\n}\n",
        encoding="utf-8",
    )


def _foreign_header(root: Path) -> None:
    """A hand-written ``u_core.h`` the manifest does not declare: what `jm
    bind` is for (gh-2072). In the template contract `bind` parses, spelled
    with the project's ``c_prefix`` stem."""
    path = root / "native/inc/p/u/u_core.h"
    path.parent.mkdir(parents=True)
    path.write_text(
        "#ifndef P_U_CORE_H\n#define P_U_CORE_H\n\n"
        "typedef struct\n{\n  double gain;\n} p_u_state_t;\n\n"
        "p_u_state_t *p_u_create (double gain);\n"
        "void p_u_destroy (p_u_state_t *state);\n"
        "void p_u_reset (p_u_state_t *state);\n\n"
        "static inline double\n"
        "p_u_step (const p_u_state_t *state, double x)\n"
        "{\n  return x * state->gain;\n}\n\n#endif\n",
        encoding="utf-8",
    )


def _hand_keys(root: Path) -> None:
    """The keys a whole-manifest rewrite dropped or respelt, which no verb
    writes in this spelling: a TOML ``true`` (gh-2046), an ``array_args``
    row's ``dtype`` (gh-2036), and the param keys of gh-2045: every one on
    the function, and on the method those its sacred fragment renders
    (`rank`, `str_hint` and `enum` it does not, even regenerated: gh-2121;
    `out` is the CLI's `--out-param`)."""
    cfg = C.load(root)
    k = cfg["k"]
    k["streamable"] = True
    (row,) = k["array_args"]
    row["dtype"] = row.pop("type")
    k["methods"][0]["params"][0].update(doc="The x.", elements_per_sample=1)
    fn = cfg["module"]["mod"]["functions"][0]["params"]
    fn[0].update(doc="The x.", rank=1, elements_per_sample=1,
                 str_hint="pass an array")  # fmt: skip
    fn[-1]["enum"] = "mode"
    C.save(root, cfg)


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
    # gh-2072: with a header nothing declares, which every verb on `o` must
    # leave alone and `jm bind` is for.
    "standalone": Shape(
        (),
        (("object", "o"), _impl_source, _foreign_header),
        _O | {"foreign"},
    ),
    "module": Shape((), (*_MOD, _impl_source), _OM, "mod"),
    # gh-2054: the class is importable from the package, not the module id.
    "package": Shape(
        (),
        (
            ("module", "mod", "--package", "other"),
            ("object", "o", *_M),
            _impl_source,
        ),
        _OM | {"package"},
        "mod",
    ),  # fmt: skip
    # gh-2081: the key added after `jm module`, and applied. Holds no
    # object, so only the cases that need nothing run on it.
    "package-edited": Shape(
        (),
        (("module", "mod"), _package("mod", "other"), ("apply",)),
        frozenset(),
    ),
    "dotted": Shape(
        (),
        (("module", "dsp.filt"), ("object", "o", "--module", "dsp.filt")),
        _OM | {"dotted"},
        "dsp.filt",
    ),
    # gh-1949: an object named for its module. Flat, it shares the module's
    # directory and core; dotted, only the cname names that directory, so
    # the leaf-named object is a member with its own (it declared
    # `o_core` twice), and the cname-named one is the collocated object --
    # with include dirs, which `apply` re-added to the module's CMakeLists
    # while it took the dotted id for the collocated object's name.
    "collocated": Shape(
        (), (("module", "o"), ("object", "o", "--module", "o")), _OM, "o"
    ),
    "dotted-leaf": Shape(
        (),
        (("module", "dsp.o"), ("object", "o", "--module", "dsp.o")),
        _OM | {"dotted"},
        "dsp.o",
    ),
    "dotted-cname": Shape(
        (),
        (
            ("module", "dsp.o"),
            (
                "object",
                "dsp_o",
                "--module",
                "dsp.o",
                "--extra-include-dirs",
                "${FOO_INCLUDE_DIR}",
            ),
        ),
        frozenset({"module", "dotted"}),
        "dsp.o",
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
            # gh-2074: an app's name is unique; this one's default (`p`)
            # is the C app's.
            (
                "app",
                "--object",
                "o",
                *_M,
                "--target",
                "console",
                "--name",
                "o-cli",
            ),
            ("app", "--function", "f", *_M, "--target", "pep723"),
        ),
        _OM | {"function", "apps"},
        "mod",
    ),  # fmt: skip
    # gh-2074: an app as schema 8 spelled it, one `[app]` table, then the
    # `jm upgrade` that rewrites it as `[[app]]`.
    "app-table": Shape(
        (),
        (
            ("object", "o"),
            (
                "app",
                "--object",
                "o",
                "--target",
                "c",
                "--flag",
                "gain:double:1.0:the gain",
            ),
            _app_table,
            ("upgrade",),
        ),
        frozenset({"app-table"}),
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
    # gh-2045 / gh-2046 / gh-2036: a central manifest holding keys the
    # layout-moving verbs rewrote through `_dump` and lost. Last, holding no
    # `o`, so it is the module-side home of `split-objects` and `migrate`
    # and of nothing that has one already.
    "central-keys": Shape(
        ("--no-fragments",),
        (
            _enum("mode", "off", "on"),
            ("module", "mod"),
            ("object", "k", *_M, "--array-arg", "taps:float32"),
            (
                "method",
                "k",
                "run",
                *_M,
                "--param",
                "x:float[]",
                "--out-param",
                "y:float[]",
                "--param",
                "m:int",
                "--return-type",
                "void",
            ),
            (
                "function",
                "f",
                *_M,
                "--param",
                "x:double[]",
                "--param",
                "m:int",
                "--return-type",
                "double",
            ),
            _hand_keys,
            # The keys reach k's sacred binding fragment too, so `adopt`
            # finds it as jm would render it.
            ("regenerate", "k", "--force"),
            ("apply",),
        ),
        frozenset({"central", "module"}),
        "mod",
    ),  # fmt: skip
}


# ── Cases ────────────────────────────────────────────────────────────────────


class Case(NamedTuple):
    """One run of a verb: argv, what the shape must hold, which shapes.

    In ``argv``, ``{M}`` is ``--module <id>`` when ``o`` lives in a module
    and nothing when it is standalone; ``{mod}`` is that module's id.
    ``only`` restricts the case to the named shapes; by default it runs on
    every shape that holds ``needs`` and nothing in ``without``. ``refuses``,
    when not empty, says the verb must refuse there, and is what its one
    ``error:`` line names: the route it offers instead. A verb that refuses
    on some shapes only is two cases: one ``without`` what makes it refuse,
    one that ``needs`` it and ``refuses`` (gh-2075).
    """

    argv: tuple
    needs: frozenset = frozenset({"o"})
    only: "frozenset | None" = None
    refuses: tuple = ()
    without: frozenset = frozenset()


def _case(*argv: str, needs=("o",), only=None, refuses=(), without=()) -> Case:
    return Case(
        argv,
        frozenset(needs),
        frozenset(only) if only else None,
        refuses,
        frozenset(without),
    )


#: The placements a flag variant runs on: what decides where its files go.
BASE = ("standalone", "module", "package")

_FN = ("--param", "x:double", "--return-type", "double")

#: What `jm bind` offers instead of a declared component (gh-2072).
_BIND_ROUTE = ("`jm regenerate o`", "`jm apply`")

#: What `jm remove` offers instead of removing what the `apps` shape's apps
#: are built from: each app's own removal (gh-2075).
_APP_ROUTE = {
    "o": ("`jm remove app p`", "`jm remove app o-cli`"),
    "f": ("`jm remove app f`",),
    "mod": ("`jm remove app p`", "`jm remove app o-cli`", "`jm remove app f`"),
}

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
        # gh-2064: into a package another module already fills, and one
        # that does not exist yet.
        "package": _case("module", "a", "--package", "other", needs=(),
                         only=("package", "standalone")),
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
        "object": _case("remove", "object", "o", "--force",
                        without=("apps",)),
        "module": _case("remove", "module", "{mod}", "--force",
                        needs=("o", "module"), without=("apps",)),
        # gh-2075: a component an app is built from stays until its apps
        # go, and the refusal names each app's own removal.
        "an-apps-object": _case("remove", "object", "o", "--force",
                                needs=("o", "apps"),
                                refuses=_APP_ROUTE["o"]),
        "an-apps-module": _case("remove", "module", "{mod}", "--force",
                                needs=("o", "apps"),
                                refuses=_APP_ROUTE["mod"]),
        "an-apps-function": _case("remove", "function", "f", "--module",
                                  "{mod}", "--force", needs=("apps",),
                                  refuses=_APP_ROUTE["f"]),
        # gh-2074: an app is removed by its name.
        "app": _case("remove", "app", "p", "--force", needs=("apps",)),
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
                          "--force", needs=("o", "function"),
                          without=("apps",)),
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
        # gh-1888: the length a scalar holds, named on the command line.
        "out-type-length": _case(
            "function", "g", "--module", "{mod}", "--param", "n:size_t",
            "--out-type", "float[n]", needs=("o", "module"), only=BASE),
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
        "c": _case("app", "--object", "o", "{M}", "--target", "c",
                   without=("apps",)),
        "pep723": _case("app", "--object", "o", "{M}", "--target",
                        "pep723", only=BASE),
        "console": _case("app", "--object", "o", "{M}", "--target",
                         "console", "--name", "o-cli", "--flag",
                         "gain:double:1.0:the gain", "--command",
                         "run:run it", only=BASE),
        "c-argv": _case("app", "--object", "o", "{M}", "--target", "c",
                        "--argc-argv", only=BASE),
        "function": _case("app", "--function", "f", "--module", "{mod}",
                          "--target", "pep723", needs=("function",),
                          without=("apps",)),
        # gh-2074: a second app is APPENDED beside the first, and the App
        # block holds both C executables.
        "beside-the-others": _case("app", "--function", "f", "{M}",
                                   "--target", "c", "--name", "f-c",
                                   needs=("apps",)),
        # ...and an app's name is unique: a taken one is refused, naming
        # the taken app's removal, and a taken DEFAULT one names `--name`.
        "a-taken-name": _case("app", "--object", "o", "{M}", "--target",
                              "pep723", "--name", "f", needs=("apps",),
                              refuses=("`jm remove app f`",)),
        "a-taken-default-name": _case(
            "app", "--object", "o", "{M}", "--target", "c",
            needs=("apps",), refuses=("`--name`", "`jm remove app p`")),
        # A console app's module is its package's `cli.py`: a second one
        # there would overwrite the first's.
        "a-taken-console-module": _case(
            "app", "--function", "f", "{M}", "--target", "console",
            "--name", "f-cli", needs=("apps",),
            refuses=("`jm remove app o-cli`",)),
    },
    "perf": {"perf": _case("perf")},
    "upgrade": {
        "upgrade": _case("upgrade", needs=()),
        # gh-2074: the shape ends in the upgrade that rewrote `[app]`; a
        # second one is a fixed point.
        "an-app-table": _case("upgrade", needs=("app-table",)),
    },
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
        # gh-2072: a component the manifest declares is `apply`'s to render.
        # `bind` would render it from the header alone, dropping what only
        # the manifest says; it refuses, in both modes, from one predicate.
        "declared": _case("bind", "o", refuses=_BIND_ROUTE),
        "check-declared": _case("bind", "o", "--check",
                                refuses=_BIND_ROUTE),
        # What `bind` is for: a header the manifest does not declare.
        "undeclared": _case("bind", "u", needs=("foreign",)),
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


# ── The ratchet ──────────────────────────────────────────────────────────────
#
# Every run red when this gate landed, keyed (verb, case, shape) -- or
# ("shape", name, "") / ("new", name, "") -- and split by the issue each
# finding belongs to. A run must report EXACTLY its entry's findings: one
# that has gone away is a fix, and its part of the entry must go with it in
# the same change; one that is new is a new red, which needs a fix or an
# issue of its own. So an entry fails for its issue's cause or not at all
# (gh-1652), and the ratchet can only shrink.
#
# One FILE per issue (gh-2108). As one dict literal, every fix deleted rows
# beside another issue's, so any two fix PRs conflicted -- six hand
# resolutions in one batch -- and one clean auto-merge kept a new row naming
# a findings constant its sibling had deleted: a NameError at import. A file
# is named for its issue and spells its findings out, so a fix deletes its
# own file, or its own lines in it, and touches nothing another issue owns.

#: ``gh-<n>.toml`` per issue: ``[[red]]`` tables, each the ``runs`` that
#: report exactly ``found``.
RATCHET_DIR = Path(__file__).with_name("gh2057_ratchet")
_ISSUE_FILE = re.compile(r"gh-\d+\.toml")
_RED_KEYS = {"runs", "found"}


def ratchet_file(issue: str) -> Path:
    """The one file that holds *issue*'s entries, and that its fix edits."""
    return RATCHET_DIR / f"{issue}.toml"


def load_ratchet(
    where: Path,
) -> "dict[tuple[str, str, str], dict[str, frozenset[str]]]":
    """Every issue file in *where*, as ``{run: {issue: findings}}``.

    Strict, because whatever this skipped would be an entry nothing
    enforces: a file not named ``gh-<n>.toml``, anything but ``[[red]]``
    tables, a table with a key other than ``runs`` and ``found`` or with
    either empty, a run that is not three names, and a run its issue lists
    twice are each refused, naming the file. A dotfile is an editor's, not
    an entry.
    """
    out: "dict[tuple[str, str, str], dict[str, frozenset[str]]]" = {}
    for path in sorted(where.iterdir()):
        if path.name.startswith("."):
            continue
        if not _ISSUE_FILE.fullmatch(path.name):
            raise ValueError(f"{path}: not gh-<n>.toml, so it names no issue")
        data = C.tomllib.loads(path.read_text(encoding="utf-8"))
        tables = data.pop("red", None)
        if data or not isinstance(tables, list) or not tables:
            raise ValueError(f"{path}: [[red]] tables and nothing else")
        seen: "set[tuple]" = set()
        for red in tables:
            runs, found = red.get("runs"), red.get("found")
            if set(red) != _RED_KEYS or not runs or not found:
                raise ValueError(
                    f"{path}: a [[red]] is a non-empty `runs` and `found`"
                    f" and nothing else, not {sorted(red)}"
                )
            # A bare string is a sequence too: one would read as its letters.
            if not isinstance(found, list) or not all(
                isinstance(f, str) for f in found
            ):
                raise ValueError(f"{path}: `found` is a list of strings")
            for run in runs if isinstance(runs, list) else [runs]:
                key = tuple(run) if isinstance(run, list) else ()
                if len(key) != 3 or not all(isinstance(s, str) for s in key):
                    raise ValueError(f"{path}: a run is three names: {run}")
                if key in seen:
                    raise ValueError(f"{path}: {key} is listed twice")
                seen.add(key)
                out.setdefault(key, {})[path.stem] = frozenset(found)
    return out


RATCHET = load_ratchet(RATCHET_DIR)


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
    return case.needs <= shape.has and not case.without & shape.has


def _runs() -> "list[tuple[str, str, str]]":
    """Every (verb, case, shape) the gate can run: each case on every shape
    that admits it."""
    return [
        (verb, cname, sname)
        for verb, cases in CASES.items()
        for cname, case in cases.items()
        for sname in SHAPES
        if _admits(case, sname)
    ]


#: The environment variable that runs the whole of `_runs()`. Set on one CI
#: leg (`.github/workflows/ci.yml`, held there by
#: `test_the_whole_matrix_has_an_execution_home`), and by anyone who asks.
FULL_ENV = "JM_VERB_GATE_FULL"


def _home(verb: str, cname: str) -> "set[str]":
    """The shapes a case runs on when the whole matrix does not: the first
    shape admitting it on each side of the one axis most of this class's
    bugs have turned on -- a standalone object or one in a module (gh-1984,
    gh-2054, gh-2070, gh-2071)."""
    out = set()
    for in_module in (False, True):
        for sname, shape in SHAPES.items():
            if ("module" in shape.has) == in_module and _admits(
                CASES[verb][cname], sname
            ):
                out.add(sname)
                break
    return out


def _params():
    """The runs `make test` makes. The whole matrix on the leg that sets
    `FULL_ENV`; elsewhere each case on its `_home` shapes, plus every run
    the ratchet names, so a ratcheted issue's fix fails on every leg until
    its entry goes. The whole matrix added 5-8 minutes to a 4-core CI leg
    and timed Coverage out at 45 (gh-2057); this is about a third of it."""
    full = os.environ.get(FULL_ENV) == "1"
    for verb, cname, sname in _runs():
        if (
            full
            or sname in _home(verb, cname)
            or (verb, cname, sname) in (RATCHET)
        ):
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


def _rewritten_by_apply(root: Path) -> "dict[str, bytes]":
    """The files `apply` renders whole from the manifest alone.

    `_createonly`'s RECONCILED kind: not DERIVED, which follows the tree's
    own sacred files by design and so legitimately differs from a rebuild.
    """
    out = {}
    for rel, data in _tree(root).items():
        rule = CO.classify(rel, root)
        if rule is not None and rule.kind == CO.RECONCILED:
            out[rel] = data
    return out


def fresh(root: Path, shape: Shape, base: Path) -> "frozenset[str]":
    """(c) The tree `apply` left, against a fresh `jm new` given the same
    manifest and an `apply`.

    It tests the incremental path against the from-scratch one, and it is
    the oracle that sees a file nothing derives any more: neither `status`
    (gh-1986) nor `apply` reports one that `apply` does not delete. Run
    after `findings`, so the tree is `apply`'s.
    """
    _ok(base, ("new", "p", *shape.new))
    other = base / "p"
    for path in _manifest_fragments(other):
        path.unlink()
    for path in (root / C.FILENAME, *_manifest_fragments(root)):
        rel = path.relative_to(root)
        (other / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, other / rel)
    out = set()
    a = run_cli("apply", cwd=other)
    if a.returncode:
        # A refused `apply` writes nothing (gh-1867), so `other` holds only
        # what `jm new` wrote: there is no from-scratch render to compare.
        (last, *_) = reversed(a.stderr.strip().splitlines() or ["?"])
        return frozenset({f"fresh:refused {last}"})
    ours, theirs = _rewritten_by_apply(root), _rewritten_by_apply(other)
    for rel in set(ours) | set(theirs):
        if ours.get(rel) != theirs.get(rel):
            mark = (
                "only-tree"
                if rel not in theirs
                else "only-fresh"
                if rel not in ours
                else "~"
            )
            out.add(f"fresh:{mark} {rel}")
    return frozenset(out)


def _ok(root: Path, argv) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (list(argv), (r.stdout + r.stderr)[-3000:])


def _refused(root: Path, argv, route: tuple) -> None:
    """*argv* refuses: non-zero, one ``error:`` line naming every part of
    *route*, and the tree byte-identical -- so what the oracles see next is
    the shape's own tree."""
    before = _tree(root)
    r = run_cli(*argv, cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode and len(errors) == 1, (
        list(argv),
        (r.stdout + r.stderr)[-3000:],
    )
    missing = [part for part in route if part not in errors[0]]
    assert not missing, f"the refusal does not name {missing}: {errors[0]}"
    assert _tree(root) == before, f"the refused {list(argv)} wrote"


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
    # gh-2074: and every kind `jm remove` takes -- a word, not a flag, and a
    # render path of its own all the same.
    removed = {case.argv[1] for case in CASES["remove"].values()}
    missing += [("remove", k) for k in _cli_remove._KINDS if k not in removed]
    assert not missing, f"flags no case passes: {missing}"
    assert not stale, f"UNCOVERED_FLAGS entries to delete: {stale}"


def test_the_whole_matrix_has_an_execution_home():
    """`FULL_ENV` is set on a leg that exists, on the step that runs
    `make test`: a matrix that runs nowhere is a gate that gates nothing.
    Read by hand, as `test_own_ci_matrix` reads the matrix."""
    block = _job_block(CI_YML.read_text(encoding="utf-8"), "test")
    steps = re.split(r"\n      - ", block)
    (suite,) = [s for s in steps if s.startswith("run: make test\n")]
    m = re.search(rf"\n {{8}}env:\n {{10}}{FULL_ENV}: (.*?)(?=\n {{0,8}}\S|$)",
                  suite, re.S)  # fmt: skip
    assert m, f"the `make test` step sets no {FULL_ENV}"
    expr = " ".join(m.group(1).split())
    assert expr.endswith("&& '1' || '' }}"), expr
    os_axis, py_axis = _axes()
    leg = dict(re.findall(r"matrix\.([\w-]+) == '([^']+)'", expr))
    assert leg.get("os") in os_axis, expr
    assert leg.get("python-version") in py_axis, expr


def test_every_ratchet_entry_names_a_run_and_an_issue():
    """An entry for a (verb, case, shape) that never runs would sit in the
    ratchet forever, reading as known breakage; so would one citing no
    issue, or expecting nothing."""
    ids = set(_runs())
    ids |= {("new", c, "") for c in NEW_CASES}
    ids |= {("shape", s, "") for s in SHAPES}
    for key, by_issue in RATCHET.items():
        assert key in ids, f"{key} is not a run of this gate"
        assert by_issue, key
        for issue, found in by_issue.items():
            assert re.fullmatch(r"gh-\d+", issue), (key, issue)
            assert found, (key, issue)
        parts = list(by_issue.values())
        overlap = [f for i, a in enumerate(parts) for f in a if any(
            f in b for b in parts[i + 1:])]  # fmt: skip
        assert not overlap, f"{key}: a finding cited for two issues"


_ONE_RED = '[[red]]\nruns = [["shape", "s", ""]]\nfound = ["x"]\n'

#: id -> (file name, its text, what the refusal says).
_NOT_A_RATCHET_FILE = {
    "not-toml": ("gh-1.txt", _ONE_RED, "names no issue"),
    "not-an-issue": ("notes.toml", _ONE_RED, "names no issue"),
    "no-table": ("gh-1.toml", "# nothing\n", "tables and nothing else"),
    "a-shared-key": (
        "gh-1.toml", 'shared = ["x"]\n' + _ONE_RED, "tables and nothing else"),
    "a-stray-key": ("gh-1.toml", _ONE_RED + 'also = ["y"]\n', "nothing else,"),
    "nothing-found": ("gh-1.toml", _ONE_RED.replace('["x"]', "[]"), "non-em"),
    "found-a-string": (
        "gh-1.toml", _ONE_RED.replace('["x"]', '"xyz"'), "list of strings"),
    "a-short-run": ("gh-1.toml", _ONE_RED.replace(', ""', ""), "three names"),
    "a-run-a-string": (
        "gh-1.toml", _ONE_RED.replace('["shape", "s", ""]', '"abc"'),
        "three names"),
    "a-run-twice": ("gh-1.toml", _ONE_RED * 2, "listed twice"),
}  # fmt: skip


@pytest.mark.parametrize(
    "name, text, why",
    _NOT_A_RATCHET_FILE.values(),
    ids=_NOT_A_RATCHET_FILE.keys(),
)
def test_a_ratchet_file_is_one_issues_and_nothing_else(
    tmp_path, name, text, why
):
    """What the loader would otherwise skip is an entry nothing enforces,
    so each such file is refused, by name (gh-2108)."""
    (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=why):
        load_ratchet(tmp_path)


def test_a_ratchet_file_reads_as_its_issue(tmp_path):
    """The issue is the file's name, and an editor's dotfile is skipped."""
    (tmp_path / "gh-7.toml").write_text(_ONE_RED, encoding="utf-8")
    (tmp_path / ".gh-7.toml.swp").write_text("junk", encoding="utf-8")
    assert load_ratchet(tmp_path) == {("shape", "s", ""): {"gh-7": {"x"}}}


def test_two_issues_fixes_merge_in_either_order(tmp_path):
    """gh-2108: the fix for one issue -- deleting its file -- merges with
    the fix for another in either order, with nothing to resolve, and
    leaves exactly the other issues' entries. On the real files, every
    pair adjacent by number (adjacency is what conflicted as one dict),
    plus two of its own so the check never runs out of pairs."""
    repo = Repo(tmp_path / "r")
    where = repo.root / RATCHET_DIR.name
    shutil.copytree(RATCHET_DIR, where)
    for n in (1, 2):
        (where / f"gh-{n}.toml").write_text(_ONE_RED, encoding="utf-8")
    repo.commit("base")
    every = load_ratchet(where)
    issues = sorted(
        {i for by_issue in every.values() for i in by_issue},
        key=lambda i: int(i[3:]),
    )
    for issue in issues:
        path = ratchet_file(issue).relative_to(RATCHET_DIR.parent)
        repo.branch(issue, lambda root, p=path: (root / p).unlink())
    for a, b in zip(issues, issues[1:]):
        rest = {
            k: {i: f for i, f in v.items() if i not in (a, b)}
            for k, v in every.items()
        }
        rest = {k: v for k, v in rest.items() if v}
        for first, second in ((a, b), (b, a)):
            assert repo.conflicts(first, second) == [], (first, second)
            repo.git("checkout", "-q", Repo.MERGED)
            assert load_ratchet(where) == rest, (first, second)
            repo.git("checkout", "-q", "main")


def _check(key: "tuple[str, str, str]", found: "frozenset[str]") -> None:
    """*found* must be exactly what the ratchet expects for *key*."""
    by_issue = RATCHET.get(key, {})
    fixed = [issue for issue, part in by_issue.items() if not part & found]
    expected = frozenset().union(*by_issue.values())
    where = ", ".join(
        ratchet_file(i).relative_to(RATCHET_DIR.parent.parent).as_posix()
        for i in fixed
    )
    assert not fixed, (
        f"{key} no longer reports what {', '.join(fixed)} ratcheted: delete"
        f" the run from {where} (the ratchet only shrinks)."
    )
    assert found == expected, (
        f"{key}: the tree differs from what `jm apply` writes"
        + (f" (ratcheted: {', '.join(by_issue)})" if by_issue else "")
        + ".\n  new:  "
        + "\n        ".join(sorted(found - expected))
        + "\n  gone: "
        + "\n        ".join(sorted(expected - found))
    )


# ── The gate ─────────────────────────────────────────────────────────────────


#: The verbs (c) runs after: the ones that take things away, where a file
#: nothing derives any more is left behind. On `jm new` it would compare a
#: tree with one built from the same flags and the same manifest -- itself.
FRESH_VERBS = frozenset({"remove"})


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_the_shape_leaves_what_apply_writes(tmp_path, name):
    """Every shape is a sequence of verbs: it ends in sync, or a verb
    below would be blamed for what the shape left."""
    (tmp_path / "tree").mkdir()
    (tmp_path / "fresh").mkdir()
    root = _build(tmp_path / "tree", SHAPES[name])
    found = findings(root) | fresh(root, SHAPES[name], tmp_path / "fresh")
    _check(("shape", name, ""), found)


@pytest.mark.parametrize("name", sorted(NEW_CASES))
def test_new_leaves_what_apply_writes(tmp_path, name):
    _ok(tmp_path, ("new", "p", *NEW_CASES[name]))
    _check(("new", name, ""), findings(tmp_path / "p"))


@pytest.mark.parametrize("verb, cname, sname", list(_params()))
def test_the_verb_leaves_what_apply_writes(
    shape_copy, tmp_path_factory, verb, cname, sname
):
    case = CASES[verb][cname]
    shape = SHAPES[sname]
    root = shape_copy(sname)
    if case.refuses:
        _refused(root, _expand(case.argv, shape), case.refuses)
    else:
        _ok(root, _expand(case.argv, shape))
    found = findings(root)
    if verb in FRESH_VERBS:
        found |= fresh(root, shape, tmp_path_factory.mktemp("fresh"))
    _check((verb, cname, sname), found)


# ── A verb that fails writes nothing (gh-1867, gh-2040) ──────────────────────


def _failure_params():
    """Each case on its `_home` shapes; on every shape that admits it where
    `FULL_ENV` asks for the whole matrix. A run is one verb and no oracle:
    the 234 home runs took 27 s on one core at a load average of 40
    (2026-10-08, `pytest -p no:xdist -k fails_after`)."""
    full = os.environ.get(FULL_ENV) == "1"
    for verb, cname, sname in _runs():
        if full or sname in _home(verb, cname):
            yield pytest.param(
                verb, cname, sname, id=f"{verb}-{cname}@{sname}"
            )


#: Cases that run a read-only MODE of a mutating command (`_cli.MUTATING`
#: names them): each exits with its report, so it never reaches the success
#: the failure is injected at -- and writes nothing to put back.
READ_ONLY_MODES = frozenset({("adopt", "check")})

#: Cases whose `_home` shapes already hold what they would write -- each
#: shape is in sync with its manifest -- so the failure injected after
#: their last write finds nothing written. On its home shapes every other
#: case has written something by then, or its run says so: the comparison
#: would otherwise hold of a tree nothing touched. (Off them, under
#: `FULL_ENV`, a case may meet a shape that already holds its change: `jm
#: app` on the shape that has the app.)
WRITES_NOTHING = frozenset(
    {
        ("apply", "apply"),
        ("apply", "only"),
        ("upgrade", "upgrade"),
        # gh-2074: the second `upgrade` of a manifest that `AppRows` has
        # already rewritten is a fixed point.
        ("upgrade", "an-app-table"),
        ("adopt", "packaging"),
    }
)


def _tree_and_dirs(root: Path) -> tuple:
    """`_tree`, and every directory: a directory left behind, or one gone,
    is a change too."""
    dirs = frozenset(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_dir() and not _EXCLUDED & set(p.relative_to(root).parts)
    )
    return _tree(root), dirs


@pytest.mark.parametrize("verb, cname, sname", list(_failure_params()))
def test_a_verb_that_fails_after_writing_writes_nothing(
    shape_copy, monkeypatch, verb, cname, sname
):
    """(d) A mutating verb that fails leaves the tree it found, the
    author's own edit to every ``_core.c`` included: exit 1, one
    ``error:`` line, every byte and directory as it was.

    The failure is injected at the last moment one can come: where the
    command would have succeeded (`_undo.commit`), so after every write it
    makes. A real refusal comes earlier and has less to put back; the
    issues' own -- `regenerate` losing its component, `jm method` and `jm
    property` half-writing a member -- are held in
    `test_gh1867_failed_verb_writes_nothing.py` and
    `test_gh1884_str_step_type_refused.py`."""
    from just_makeit import _undo
    from just_makeit._report import Refusal

    case, shape = CASES[verb][cname], SHAPES[sname]
    root = shape_copy(sname)
    for core in sorted(root.glob("native/src/*/*_core.c")):
        core.write_bytes(core.read_bytes() + b"/* the author's */\n")
    before = _tree_and_dirs(root)
    fired = []

    def fail() -> None:
        fired.append(_tree_and_dirs(root) != before)
        raise Refusal("injected: the command failed after its last write")

    monkeypatch.setattr(_undo, "commit", fail)
    r = run_cli(*_expand(case.argv, shape), cwd=root)

    assert _tree_and_dirs(root) == before, (r.stdout + r.stderr)[-3000:]
    if case.refuses or (verb, cname) in READ_ONLY_MODES:
        assert not fired, f"{verb} {cname} reached success"
        return
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert fired and r.returncode == 1 and len(errors) == 1, (
        fired,
        (r.stdout + r.stderr)[-3000:],
    )
    assert "injected" in errors[0], errors
    wrote = fired[0]
    assert sname not in _home(verb, cname) or wrote != (
        (verb, cname) in WRITES_NOTHING
    ), (
        f"{verb} {cname} wrote {'something' if wrote else 'nothing'} on"
        f" {sname}: {'delete it from' if wrote else 'it belongs in'}"
        " WRITES_NOTHING"
    )
