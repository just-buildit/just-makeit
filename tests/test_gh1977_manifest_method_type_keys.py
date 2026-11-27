"""gh-1977: every method key that names a C type is checked on the manifest path.

``C.manifest_type_errors`` is ``apply``'s gate in front of the render: a type
the binding cannot convert is refused there, with one ``error:`` line, before
anything is written. It checked ``arg_type``, ``return_type``, ``params`` and
``result_fields``, and nothing else. Three more keys name a C type and reached
a type table unchecked, so a row the command line refuses crashed ``status``
and ``apply`` with a bare ``KeyError``:

- a method's ``out_type`` (``_CTYPE_TO_NPY[out_type]``) -- and a module
  function's, which the same check exempted from ``return_type`` without
  ever checking itself;
- a method's ``multi_output`` (``_CTYPE_META[rt]["zero"]``);
- a method's ``extra_args``, the synonym of ``params`` the replay forwards
  when it is set (``_param_type_errors`` read only ``params``). Since
  gh-2208 ``C.load`` reads it as ``params`` for every face, and the
  refusal still names it ``extra_arg``.

``--out-type`` and ``--multi-output`` each held an inline membership test, on
two command lines apiece; ``_types.is_out_type`` and
``_types.is_multi_output_type`` are now the one answer for those four sites
and for the manifest.

One row, one refusal. A variable-output method whose ``out_type`` is ``void``
is gh-1885's row: ``_outbuf.element_why_not`` refuses it at the render in
words about the output element, and ``test_gh1885_void_variable_output``
holds those words. The new check leaves that row to it rather than refusing
it a second time, differently.

What the sweep cannot see. It finds a type that reaches a table as a
``KeyError``; a key read through ``.get(..., fallback)`` would turn the same
spelling into a silent default, which only compiling the result shows.

GATE: each row the command line refuses -- the issue's four, ``extra_args``,
      a function's ``out_type``, a view method's, and a bare-string
      ``multi_output`` -- exits 1 from ``apply`` and ``status --check`` with
      one ``error:`` line naming the row and the key, no traceback, and the
      tree byte-identical; a sweep over every key in ``_keys.METHOD_KEYS``
      and ``_keys.FUNCTION_KEYS`` (not a list of the type keys) finds no
      unregistered spelling reaching a type table, so a new key that names a
      type is covered; every spelling the shared predicates accept passes
      the manifest gate; and ``jm method`` / ``jm object`` / ``jm function``
      refuse exactly the spellings the manifest refuses.

GATE (gh-2208): an ``extra_args`` method whose header declaration carries an
      authored block -- a standalone object and a module object -- applies,
      reports clean, names ``k`` in every ``.pyi``, and compiles, imports and
      answers a call passing ``k``; a save writes ``extra_args`` back, a
      deferred load reads it as a load from disk does, and a row with both
      spellings is refused.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _compilers import default_cc

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _keys
from just_makeit import _types as T

#: A spelling no table registers, distinctive enough that a ``KeyError``
#: naming it can only come from the probe.
PROBE = "gh1977_unregistered_t"

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: A plain method: a scalar in, a scalar out, nothing that changes its shape.
BASE_METHOD = {"name": "m", "arg_type": "float", "return_type": "float"}


def _toml_value(value: object) -> str:
    """The three value shapes a type takes in a manifest, as TOML."""
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    items = []
    for v in value:  # type: ignore[union-attr]
        if isinstance(v, dict):
            inner = ", ".join(f'{k} = "{x}"' for k, x in v.items())
            items.append("{ " + inner + " }")
        else:
            items.append(f'"{v}"')
    return "[" + ", ".join(items) + "]"


def _row_text(row: dict) -> str:
    return "".join(f"{k} = {_toml_value(v)}\n" for k, v in row.items())


#: Where each face's row is written, and how to read it back once loaded.
#: Every target appends to a fragment whose LAST table is the row's parent,
#: and `_landed` proves the row arrived there rather than in some sub-table.
TARGETS = {
    "method": ("objects/o.toml", "[[o.methods]]\n"),
    "view": ("objects/vo.toml", "[[vo.views.methods]]\n"),
    "function": ("modules/mm.toml", ""),
}


def _write_row(root: Path, target: str, row: dict) -> None:
    rel, header = TARGETS[target]
    with open(root / rel, "a", encoding="utf-8") as fh:
        fh.write("\n" + header + _row_text(row))


def _loaded_row(root: Path, target: str, name: str) -> dict:
    """The row as TOML parses the fragment -- not through `C.load`, which
    validates some keys itself and would refuse a probe before it is read."""
    rel, _ = TARGETS[target]
    with open(root / rel, "rb") as fh:
        data = tomllib.load(fh)
    if target == "method":
        rows = data["o"]["methods"]
    elif target == "view":
        rows = [m for v in data["vo"]["views"] for m in v.get("methods", [])]
    else:
        rows = data["module"]["mm"]["functions"]
    return next((r for r in rows if r.get("name") == name), {})


def _landed(root: Path, target: str, row: dict) -> None:
    """The row is in the manifest as written -- a probe that landed in the
    wrong table would make every assertion below vacuous."""
    got = _loaded_row(root, target, row["name"])
    for key, value in row.items():
        assert got.get(key) == value, (target, key, got)


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, and every directory as an empty marker."""
    out = {}
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else b"<dir>"
    return out


def _run_ok(root: Path, *args: str) -> None:
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    """One object, one module function and one view, built once.

    ``--no-c-prefix`` so the view's ``create_fn`` is the name written here.
    """
    base = tmp_path_factory.mktemp("gh1977_blank")
    _run_ok(base, "new", "p", "--no-c-prefix")
    root = base / "p"
    _run_ok(root, "object", "o")
    _run_ok(root, "module", "mm")
    _run_ok(root, "function", "f", "--module", "mm")
    _run_ok(root, "module", "vm")
    _run_ok(root, "object", "vo", "--module", "vm")
    _run_ok(
        root, "view", "vo", "Peek", "--module", "vm", "--create-fn", "vo_peek"
    )
    _run_ok(root, "status", "--check")
    return root


def _copy(blank: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "p"
    shutil.copytree(blank, dst)
    return dst


# -- the rows: refused by apply and status, and nothing written --------------

#: (target, the keys added to the face's base row, where, the key named).
#: The first four are the issue's; the rest are the same class, found by the
#: sweep below or by reading what else `manifest_type_errors` skipped.
ROWS = [
    pytest.param(
        "method",
        {"multi_output": ["wat_t"]},
        "'o' method 'm'",
        "multi_output",
        id="issue-multi-output-unregistered",
    ),
    pytest.param(
        "method",
        {"out_type": "void"},
        "'o' method 'm'",
        "out_type",
        id="issue-out-type-void",
    ),
    pytest.param(
        "method",
        {"out_type": "wat_t"},
        "'o' method 'm'",
        "out_type",
        id="issue-out-type-unregistered",
    ),
    pytest.param(
        "method",
        {"multi_output": ["void"]},
        "'o' method 'm'",
        "multi_output",
        id="issue-multi-output-void",
    ),
    # The predicate's boundary: a registered scalar with no numpy dtype, and
    # an array spelling of an element that has one. Both crashed on a plain
    # method; `--out-type` refuses both.
    pytest.param(
        "method",
        {"out_type": "bool"},
        "'o' method 'm'",
        "out_type",
        id="out-type-registered-scalar-without-dtype",
    ),
    pytest.param(
        "method",
        {"out_type": "float[]"},
        "'o' method 'm'",
        "out_type",
        id="out-type-array-spelling",
    ),
    pytest.param(
        "method",
        {"out_type": "wat_t", "variable_output": True},
        "'o' method 'm'",
        "out_type",
        id="out-type-unregistered-variable-output",
    ),
    pytest.param(
        "method",
        {"multi_output": "float"},
        "'o' method 'm'",
        "multi_output",
        id="multi-output-bare-string",
    ),
    pytest.param(
        "method",
        {"extra_args": [{"name": "k", "type": "wat_t"}]},
        "'o' method 'm'",
        "extra_arg",
        id="extra-args-unregistered",
    ),
    pytest.param(
        "view",
        {"out_type": "wat_t"},
        "'vo' view 'Peek' method 'm'",
        "out_type",
        id="view-method-out-type",
    ),
    pytest.param(
        "view",
        {"multi_output": ["wat_t"]},
        "'vo' view 'Peek' method 'm'",
        "multi_output",
        id="view-method-multi-output",
    ),
    pytest.param(
        "function",
        {"out_type": "wat_t"},
        "module 'mm' function 'f'",
        "out_type",
        id="function-out-type-unregistered",
    ),
    pytest.param(
        "function",
        {"out_type": "void"},
        "module 'mm' function 'f'",
        "out_type",
        id="function-out-type-void",
    ),
]


def _base(target: str) -> dict:
    """The face's row before the keys under test: a function is the ``f``
    the fixture declared, a method a fresh plain one."""
    return {"name": "f"} if target == "function" else dict(BASE_METHOD)


def _poison(root: Path, target: str, keys: dict) -> None:
    row = {**_base(target), **keys}
    if target == "function":
        # `f` exists; append its new keys to its table, the fragment's last.
        rel, _ = TARGETS[target]
        with open(root / rel, "a", encoding="utf-8") as fh:
            fh.write(_row_text(keys))
    else:
        _write_row(root, target, row)
    _landed(root, target, row)


@pytest.mark.parametrize("command", [["apply"], ["status", "--check"]])
@pytest.mark.parametrize("target,keys,where,key", ROWS)
def test_refused_with_one_error_and_nothing_written(
    blank, tmp_path, target, keys, where, key, command
):
    root = _copy(blank, tmp_path)
    _poison(root, target, keys)
    before = _snapshot(root)

    r = run_cli(*command, cwd=root)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "KeyError" not in r.stderr, r.stderr
    # `status` indents the replay's own lines under its one `error:`.
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    named = [
        ln
        for ln in r.stderr.splitlines()
        if ln.lstrip().startswith(f"{where}: {key}")
    ]
    assert named, f"no line names {where} and `{key}`:\n{r.stderr}"
    assert _snapshot(root) == before, f"jm {' '.join(command)} wrote"


def test_a_void_out_type_on_an_array_result_is_gh1885s_alone(blank, tmp_path):
    """One row, one refusal: the element refusal owns it, and the new check
    does not add a second line in other words before it."""
    root = _copy(blank, tmp_path)
    _poison(root, "method", {"out_type": "void", "variable_output": True})
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert errors == [
        "error: method 'o.m' is variable_output, but its output element "
        "is 'void'."
    ], r.stderr
    assert "has no numpy equivalent" not in r.stderr, r.stderr


# -- the class: every key, not the keys known to name a type ----------------

#: The value shapes a type takes in a manifest: a scalar key's string, a list
#: key's strings (``multi_output``), and a list of ``{name, type}`` tables
#: (``params``). A key holding another shape is not a type key; a probe of
#: the wrong shape for it tests that key's own parsing, which is not this
#: gate's question -- only a ``KeyError`` naming the probe answers it.
SHAPES = {
    "string": PROBE,
    "list": [PROBE],
    "tables": [{"name": "k", "type": PROBE}],
}

SWEEP = [
    pytest.param("method", key, id=f"method-{key}")
    for key in sorted(_keys.METHOD_KEYS - {"name"})
] + [
    pytest.param("function", key, id=f"function-{key}")
    for key in sorted(_keys.FUNCTION_KEYS - {"name"})
]


@pytest.mark.parametrize("target,key", SWEEP)
def test_no_key_lets_an_unregistered_type_reach_a_type_table(
    blank, tmp_path, target, key
):
    reached = []
    for shape, value in SHAPES.items():
        root = _copy(blank, tmp_path / shape)
        _poison(root, target, {key: value})
        r = run_cli("apply", cwd=root)
        if f"KeyError: '{PROBE}'" in r.stderr.splitlines():
            reached.append(shape)
    assert not reached, (
        f"`{key}` holding an unregistered type ({', '.join(reached)}) "
        "reached a type table with no manifest refusal in front of it; "
        "check it in `_config.manifest_type_errors`, with the predicate "
        "its command-line flag asks."
    )


def test_the_sweep_is_armed():
    """It walks the vocabularies, and they hold the keys it must catch."""
    assert {"out_type", "multi_output", "extra_args"} <= _keys.METHOD_KEYS
    assert "out_type" in _keys.FUNCTION_KEYS
    assert len(SWEEP) == len(_keys.METHOD_KEYS) + len(_keys.FUNCTION_KEYS) - 2


# -- what still passes ------------------------------------------------------


def _method_cfg(**keys) -> dict:
    return {"o": {"methods": [{**BASE_METHOD, **keys}]}}


def _function_cfg(**keys) -> dict:
    return {"module": {"mm": {"functions": [{"name": "f", **keys}]}}}


@pytest.mark.parametrize("ctype", sorted(T.SUPPORTED_ARRAY_CTYPES))
def test_every_out_type_the_predicate_accepts_passes(ctype):
    for vo in (False, True):
        cfg = _method_cfg(out_type=ctype, variable_output=vo)
        assert C.manifest_type_errors(cfg) == [], (ctype, vo)
    assert C.manifest_type_errors(_function_cfg(out_type=ctype)) == []


@pytest.mark.parametrize("ctype", sorted(T.SUPPORTED_TYPES))
def test_every_multi_output_type_the_predicate_accepts_passes(ctype):
    assert C.manifest_type_errors(_method_cfg(multi_output=[ctype])) == []


@pytest.mark.parametrize(
    "keys",
    [
        # gh-128: a numpy dtype naming its length param (doppler's
        # `ciccompmf` declares exactly this).
        {"out_type": "float64[M]"},
        {"out_type": "uint8_t[n]"},
        # gh-1180: a variable-output function's text result.
        {"out_type": "str", "variable_output": True, "out_size": "n"},
    ],
    ids=["dtype-length", "ctype-length", "str-variable-output"],
)
def test_a_functions_own_out_type_grammar_passes(keys):
    assert C.manifest_type_errors(_function_cfg(**keys)) == []


def test_str_without_variable_output_says_what_it_needs():
    errors = C.manifest_type_errors(_function_cfg(out_type="str"))
    assert len(errors) == 1 and "needs variable_output = true" in errors[0]


def test_accepted_rows_apply_and_report_clean(blank, tmp_path):
    """End to end: the shapes a project really declares still build."""
    root = _copy(blank, tmp_path)
    _poison(root, "method", {"out_type": "uint8_t"})
    _write_row(
        root,
        "method",
        {
            "name": "m2",
            "arg_type": "float",
            "return_type": "float",
            "variable_output": True,
            "multi_output": ["uint8_t", "bool"],
        },
    )
    _write_row(
        root,
        "method",
        {
            "name": "m3",
            "arg_type": "float",
            "return_type": "float",
            "extra_args": [{"name": "k", "type": "int32_t"}],
        },
    )
    _run_ok(root, "apply")
    _run_ok(root, "status", "--check")


#: gh-2208: the C body each ``m3`` kernel gets, so a call that passes ``k``
#: has an answer only ``k`` can give.
_SCALE_BODY = "(void)state;\n    return x * (float)k;"

#: gh-2208: the project's own test, which calls each ``m3`` with ``k`` and
#: leaves a file behind when it ran, so a run that never collected it is
#: told apart from a run that passed it.
_CALLS_K = """\
from pathlib import Path

from p import O
from p.vm import Vo


def test_m3_takes_k():
    assert O().m3(2.0, 3) == 6.0
    assert Vo().m3(2.0, 5) == 10.0
    Path(__file__).with_suffix(".ran").write_text("ok", encoding="utf-8")
"""


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_extra_args_survive_an_authored_header_block(blank, tmp_path):
    """gh-2208, built: an ``extra_args`` method keeps its argument once its
    header declaration carries an authored Doxygen block.

    That block makes the header "enriched", which sends ``apply`` into its
    post-replay glue re-render. Only the replay translated ``extra_args``,
    so the re-render bound ``m3(x)`` against ``m3(state, x, k)``: the project
    stopped compiling, the ``.pyi`` dropped ``k``, and ``status --check``,
    rendering the same way, exited 0. A standalone object (``o``) and a
    module object (``vo``), each compiled, imported and called with ``k``.
    """
    root = _copy(blank, tmp_path)
    row = {
        "name": "m3",
        "arg_type": "float",
        "return_type": "float",
        "extra_args": [{"name": "k", "type": "int32_t"}],
    }
    _write_row(root, "method", row)
    with open(root / "objects" / "vo.toml", "a", encoding="utf-8") as fh:
        fh.write("\n[[vo.methods]]\n" + _row_text(row))
    _run_ok(root, "apply")
    for comp in ("o", "vo"):
        (header,) = root.rglob(f"{comp}_core.h")
        decl = f"float {comp}_m3({comp}_state_t *state, float x, int32_t k);"
        text = header.read_text(encoding="utf-8")
        assert text.count(decl) == 1, (decl, text)
        header.write_text(
            text.replace(decl, "/**\n * @brief Scale x by k.\n */\n" + decl),
            encoding="utf-8",
        )
        (core,) = root.rglob(f"{comp}_core.c")
        stub = "(void)state; (void)x; (void)k;\n    return (float)0.0f;"
        body = core.read_text(encoding="utf-8")
        assert body.count(stub) == 1, body
        core.write_text(body.replace(stub, _SCALE_BODY), encoding="utf-8")
    _run_ok(root, "apply")
    _run_ok(root, "status", "--check")
    # The manifest keeps the author's spelling: `load` reads the synonym,
    # and `save` writes it back as it was.
    assert "extra_args" in (root / "objects" / "o.toml").read_text("utf-8")
    stubs = "".join(
        p.read_text("utf-8") for p in sorted((root / "src").rglob("*.pyi"))
    )
    # Every class carrying m3 -- O, Vo, and the view over Vo -- names k.
    with_k = stubs.count("def m3(self, x: float, k: int)")
    assert with_k >= 2 and stubs.count("def m3(") == with_k, stubs
    probe = root / "src" / "p" / "tests" / "test_gh2208_calls_k.py"
    probe.write_text(_CALLS_K, encoding="utf-8")
    out = run_cli("test", cwd=root)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    assert probe.with_suffix(".ran").is_file(), out.stdout[-3000:]


def _with_m3(blank: Path, tmp_path: Path) -> Path:
    root = _copy(blank, tmp_path)
    _write_row(
        root,
        "method",
        {
            "name": "m3",
            "arg_type": "float",
            "return_type": "float",
            "extra_args": [{"name": "k", "type": "int32_t"}],
        },
    )
    return root


def test_a_save_writes_the_synonym_back(blank, tmp_path):
    """gh-2208: `load` reads ``extra_args`` as ``params``; a command that
    saves the manifest writes the row back as the author spelled it."""
    root = _with_m3(blank, tmp_path)
    assert C.load(root)["o"]["methods"][-1]["params"][0]["name"] == "k"
    _run_ok(
        root, "method", "o", "m4", "--arg-type", "float", "--return-type",
        "float",
    )  # fmt: skip
    m3 = _loaded_row(root, "method", "m3")
    assert m3["extra_args"] == [{"name": "k", "type": "int32_t"}], m3
    assert "params" not in m3 and C.PARAMS_SPELLED_KEY not in m3, m3


def test_script_replays_the_synonyms_argument(blank, tmp_path):
    """gh-2208's class: ``jm script`` read ``params`` alone, so the command
    it reconstructed for an ``extra_args`` method declared no argument."""
    r = run_cli("script", cwd=_with_m3(blank, tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    (m3,) = [c for c in r.stdout.split("\n\n") if " o m3 " in c]
    assert "--param k:int32_t" in m3, m3


def test_a_deferred_load_reads_the_synonym_as_disk_does(blank, tmp_path):
    """gh-2208: inside `deferred_save` a load is served from the cache
    `save` filled, which holds the author's spelling; it must read as the
    same manifest a load from disk gives (gh-764's contract)."""
    root = _with_m3(blank, tmp_path)
    want = C.load(root)
    with C.deferred_save():
        C.save(root, C.load(root))
        assert C.load(root) == want


def test_both_spellings_on_one_row_are_refused(blank, tmp_path):
    """gh-2208: ``params`` and ``extra_args`` name one list; a row with
    both is refused rather than read one way by one face and the other
    way by another."""
    root = _copy(blank, tmp_path)
    _write_row(
        root,
        "method",
        {
            "name": "m3",
            "arg_type": "float",
            "return_type": "float",
            "params": [{"name": "j", "type": "int32_t"}],
            "extra_args": [{"name": "k", "type": "int32_t"}],
        },
    )
    before = _snapshot(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "declares both params and extra_args" in r.stderr, r.stderr
    assert _snapshot(root) == before


# -- the command lines ask the same predicate ------------------------------

#: Every registered scalar -- the boundary between the two predicates runs
#: through it (``bool``, ``int``, ``const char *``) -- and three spellings
#: no face accepts.
POOL = sorted(T.SUPPORTED_TYPES | {"void", "float[]", PROBE})

#: (the command line, given the spelling; the manifest row it stands for;
#: the flag a refusal names).
FACES = {
    "method-out-type": (
        lambda x: (
            ["method", "o", "m", "--arg-type", "float"]
            + ["--return-type", "float", "--out-type", x]
        ),
        lambda x: _method_cfg(out_type=x),
        "--out-type",
    ),
    "method-multi-output": (
        lambda x: (
            ["method", "o", "m", "--arg-type", "float"]
            + ["--return-type", "float", "--multi-output", x]
        ),
        lambda x: _method_cfg(multi_output=[x]),
        "--multi-output",
    ),
    "object-multi-output": (
        lambda x: (
            ["object", "x", "--arg-type", "float", "--return-type"]
            + ["float", "--variable-output", "--multi-output", x]
        ),
        lambda x: _method_cfg(variable_output=True, multi_output=[x]),
        "--multi-output",
    ),
    # gh-1888: an array param gives the output its length, so the element
    # is the only question asked of the spelling.
    "function-out-type": (
        lambda x: (
            ["function", "g", "--module", "mm", "--param", "x:float[]"]
            + ["--out-type", x]
        ),
        lambda x: _function_cfg(out_type=x),
        "--out-type",
    ),
}


@pytest.mark.parametrize("face", sorted(FACES))
def test_the_command_line_refuses_what_the_manifest_refuses(
    blank, tmp_path, face
):
    """#1408's axis: a spelling one face takes and the other refuses is the
    defect this issue is the crashing form of."""
    command, manifest, flag = FACES[face]
    disagree = []
    for i, spelling in enumerate(POOL):
        root = _copy(blank, tmp_path / str(i))
        r = run_cli(*command(spelling), cwd=root)
        cli_ok = r.returncode == 0
        if not cli_ok:
            assert any(
                ln.startswith("error:") and flag in ln
                for ln in r.stderr.splitlines()
            ), (spelling, r.stdout + r.stderr)
        manifest_ok = not C.manifest_type_errors(manifest(spelling))
        if cli_ok != manifest_ok:
            disagree.append(f"{spelling!r}: cli={cli_ok} toml={manifest_ok}")
    assert not disagree, "\n".join(disagree)
