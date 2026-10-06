"""gh-1885: a variable-output method needs an output element.

A ``variable_output`` method returns an ARRAY of its element type, and the
binding allocates it from that element's numpy dtype. ``void`` has none, so
the render indexed ``_CTYPE_META["void"]`` and crashed with a bare
``KeyError`` -- AFTER the row was persisted. Every later ``status`` /
``apply`` then crashed on the manifest jm had just written, until
``jm remove object <name> --force``.

Two trigger families reached it:

- a void RETURN with nothing else naming the element: ``jm method o run
  --return-type void --variable-output``, or a ``consumer`` preset's
  ``jm object --variable-output``;
- a void ARG on ``jm object --variable-output`` with no return type, whose
  method takes its element from the arg type: ``--arg-type void``, or the
  ``generator`` preset.

A ``--batch`` method returns the same array one shape over, typed by its
return type alone, and ``jm method o run --batch --return-type void``
crashed the same way.

One predicate, ``_outbuf.element_why_not``, now answers for every face:
``jm method`` and ``jm object`` ask it before writing anything, and the
binding asks it before rendering -- where a row an older jm already wrote
meets it.

GATE: each trigger exits 1 with one ``error:`` line naming the method,
      and leaves the project tree byte-identical (red on main: KeyError
      with the object and the manifest row written); each preset whose
      element is void is refused the same way and every other preset
      still scaffolds -- both derived from ``_PRESETS``, not listed; the
      advice the refusals give works; and a manifest already holding such
      a row -- a void return, a variable-output method's void
      ``out_type``, a batch method's void return -- gets that refusal,
      never a traceback, from ``apply``, ``status``, ``regenerate`` and a
      later ``jm method`` on the same object.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _textio
from just_makeit._cli_object import _PRESETS, _expand_presets


def _element_of(flags: "list[str]") -> str:
    """The element ``jm object --variable-output`` gives its method.

    Restated here rather than imported, so the gate does not share the rule
    it checks: the return type when the flags give one, else the arg type,
    over the CLI's own defaults.
    """
    arg_type, return_type = "float _Complex", None
    for flag, value in zip(flags, flags[1:]):
        if flag == "--arg-type":
            arg_type = value
        elif flag == "--return-type":
            return_type = value
    return return_type or arg_type


def _preset_flags(name: str) -> "list[str]":
    return _expand_presets(["--preset", name])


#: Derived from the preset table, so a preset added later is covered by
#: whichever side of the rule its flags put it on.
VOID_PRESETS = sorted(
    n for n in _PRESETS if _element_of(_preset_flags(n)) == "void"
)
OTHER_PRESETS = sorted(set(_PRESETS) - set(VOID_PRESETS))


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, and every directory as an empty marker."""
    out = {}
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else b"<dir>"
    return out


def _run_ok(root: Path, command: str) -> None:
    r = run_cli(*command.split(), cwd=root)
    assert r.returncode == 0, (command, r.stdout + r.stderr)


def _names(line: str, method: str) -> bool:
    """Is *line* the refusal, naming *method* as ``<object>.<name>``?"""
    return (
        f"method '{method}' is " in line
        and "but its output element is 'void'." in line
    )


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    """A fresh project with one module and nothing else, built once."""
    base = tmp_path_factory.mktemp("gh1885_blank")
    _run_ok(base, "new p")
    _run_ok(base / "p", "module m")
    return base / "p"


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst / "p")
    return dst / "p"


# ── the triggers: refused, and nothing written ──────────────────────────────

#: (setup commands, trigger, the method the refusal names). Both families,
#: on both object paths -- a standalone object and a module object write
#: different files before they reach the method.
TRIGGERS = [
    pytest.param(
        [],
        "object pk3 --preset consumer --variable-output --max-out 4",
        "pk3.run",
        id="void-return-consumer-issue-repro",
    ),
    pytest.param(
        ["object o"],
        "method o run --return-type void --variable-output",
        "o.run",
        id="void-return-jm-method",
    ),
    pytest.param(
        [],
        "object v --arg-type void --variable-output",
        "v.run",
        id="void-arg-jm-object",
    ),
    pytest.param(
        [],
        "object pk3 --module m --preset consumer --variable-output",
        "pk3.run",
        id="void-return-module-object",
    ),
    pytest.param(
        [],
        "object v --module m --arg-type void --variable-output",
        "v.run",
        id="void-arg-module-object",
    ),
    pytest.param(
        ["object o --module m"],
        "method o run --return-type void --variable-output",
        "o.run",
        id="void-return-jm-method-module-object",
    ),
    pytest.param(
        ["object o"],
        "method o run --batch --return-type void",
        "o.run",
        id="void-return-batch",
    ),
    pytest.param(
        ["object o"],
        "method o run --batch --variable-output --return-type void",
        "o.run",
        id="void-return-batch-variable-output",
    ),
] + [
    pytest.param(
        [],
        f"object x --preset {name} --variable-output",
        "x.run",
        id=f"preset-{name}",
    )
    for name in VOID_PRESETS
]


@pytest.mark.parametrize("setup,trigger,method", TRIGGERS)
def test_refused_before_anything_is_written(
    blank, tmp_path, setup, trigger, method
):
    root = _copy(blank, tmp_path)
    for command in setup:
        _run_ok(root, command)
    before = _snapshot(root)

    r = run_cli(*trigger.split(), cwd=root)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    assert _names(errors[0], method), r.stderr
    after = _snapshot(root)
    changed = sorted(
        k
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    )
    assert not changed, f"a refused `jm {trigger}` wrote {changed}"


def test_the_preset_scan_is_armed():
    """Both families are in the derived set, so it cannot pass empty."""
    flags = [_preset_flags(n) for n in VOID_PRESETS]
    assert any("--return-type" in f for f in flags), VOID_PRESETS
    assert any(
        "--arg-type" in f and "--return-type" not in f for f in flags
    ), VOID_PRESETS
    assert OTHER_PRESETS, "every preset is void; nothing checks the converse"


# ── what still works: every other shape, and the advice given ──────────────

#: The refusal is about the element, so everything that names one passes:
#: every preset whose element is not void, and each remedy the two
#: refusals print -- advice that does not work is a second bug.
ACCEPTED = [
    pytest.param(
        [],
        f"object x --preset {name} --variable-output",
        id=f"preset-{name}",
    )
    for name in OTHER_PRESETS
] + [
    pytest.param(
        [],
        "object pk3 --preset consumer --return-type float "
        "--variable-output --max-out 4",
        id="object-remedy-return-type-after-preset",
    ),
    pytest.param(
        [],
        "object g --preset generator --return-type float --variable-output",
        id="object-remedy-generator",
    ),
    pytest.param(
        ["object pk3 --preset consumer"],
        "method pk3 run --variable-output --return-type float",
        id="object-remedy-add-the-method-later",
    ),
    pytest.param(
        ["object o"],
        "method o run --return-type void --out-type float --variable-output",
        id="method-remedy-out-type",
    ),
    pytest.param(
        ["object o"],
        "method o run --batch --return-type float",
        id="batch-remedy-return-type",
    ),
]


@pytest.mark.parametrize("setup,command", ACCEPTED)
def test_an_element_is_all_it_takes(blank, tmp_path, setup, command):
    root = _copy(blank, tmp_path)
    for step in setup:
        _run_ok(root, step)
    _run_ok(root, command)
    _run_ok(root, "status --check")


# ── a manifest that already holds one: a refusal, never a traceback ────────


def _edit(path: Path, pattern: str, repl: str) -> None:
    """Exactly one match, replaced -- an edit that lands nowhere would turn
    a refusal test into a no-op."""
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    assert n == 1, (path, pattern, text)
    _textio.write_text(path, new)


_VO = "method o run --return-type float --variable-output"

#: How an older jm's row lacks an element, one per way the predicate reads
#: one: (the valid method it started as, the edit that took it away).
POISONS = {
    "void-return": (
        _VO,
        r'^return_type = "float"$',
        'return_type = "void"',
    ),
    "void-out-type": (
        _VO,
        r"^variable_output = true$",
        'variable_output = true\nout_type = "void"',
    ),
    "batch-void-return": (
        "method o run --batch --return-type float",
        r'^return_type = "float"$',
        'return_type = "void"',
    ),
}


def _poisoned(blank: Path, tmp_path: Path, poison: str, module: bool) -> Path:
    """A valid array-result method, then its row edited as an older jm
    left it -- the state every later command used to crash on."""
    method, pattern, repl = POISONS[poison]
    root = _copy(blank, tmp_path)
    _run_ok(root, "object o --module m" if module else "object o")
    _run_ok(root, method)
    _edit(root / "objects" / "o.toml", pattern, repl)
    return root


def _refuses_without_traceback(root: Path, *args: str) -> None:
    r = run_cli(*args, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "KeyError" not in r.stderr, r.stderr
    # `status` indents the replay's own `error:` line under its header.
    assert any(
        ln.lstrip().startswith("error:") and _names(ln, "o.run")
        for ln in r.stderr.splitlines()
    ), r.stderr


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
@pytest.mark.parametrize("poison", sorted(POISONS))
def test_apply_and_status_refuse_and_write_nothing(
    blank, tmp_path, poison, module
):
    root = _poisoned(blank, tmp_path, poison, module)
    before = _snapshot(root)
    for command in (["apply"], ["status"], ["status", "--check"]):
        _refuses_without_traceback(root, *command)
        assert _snapshot(root) == before, f"jm {' '.join(command)} wrote"


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
def test_other_commands_reaching_the_render_refuse(blank, tmp_path, module):
    """Every other command that renders the object's methods meets the row
    too: a later ``jm method`` on it, and ``regenerate``.

    The tree is not compared for these. ``regenerate`` deletes the
    component before it re-applies -- gh-1867, which names this very
    manifest as a trigger -- and ``jm method`` persists its own new row
    before it renders the object's others.
    """
    root = _poisoned(blank, tmp_path, "void-return", module)
    _refuses_without_traceback(
        root, "method", "o", "other", "--return-type", "float"
    )
    root = _poisoned(blank, tmp_path / "regen", "void-return", module)
    _refuses_without_traceback(root, "regenerate", "o", "--force")


def test_remove_still_clears_the_row(blank, tmp_path):
    """The way out stays open: ``remove`` must not render the bad row, and
    once it is gone nothing refuses."""
    root = _poisoned(blank, tmp_path, "void-return", False)
    _run_ok(root, "remove method run --object o --force")
    assert "variable_output" not in (root / "objects" / "o.toml").read_text(
        encoding="utf-8"
    )
    _run_ok(root, "apply")
