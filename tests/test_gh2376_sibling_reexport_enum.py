"""gh-2376: a package's ``__all__`` names only what its import lines bind.

gh-2372 re-exports each enum an extension publishes from the package
``__init__.py``. In doppler's shape that put names in ``__all__`` that no line
imported: ``wfm_reader`` lives in the ``wfm`` package (gh-523 ``package =``)
and publishes enums, and ``[module.wfm.reexports]`` ALSO names
``wfm_reader = ["Reader"]``. Two writers then owned one line:

* ``wfm_reader``'s own merge writes ``from .wfm_reader import Reader, ENUM``
  and adds ``ENUM`` to ``__all__``;
* ``wfm``'s merge treated its reexport list as the whole truth for that line
  and cut it back to ``Reader``, while ``__all__`` kept ``ENUM``, protected as
  a sibling's name because protection was read BEFORE the rewrite.

``from doppler.wfm import *`` then raised ``AttributeError: FOLLOW_END``.

The fix: a reexport naming a package sibling adds to that sibling's line and
never shrinks it, since the sibling owns the line and prunes it itself. Then
no rewrite takes a name off a sibling's line, so the names protected in
``__all__`` are exactly the names that line binds. Reading the protected set
after the rewrite instead was measured as redundant with this (its sabotage
stayed green), so it is not done.

The apply-level tests pass on the broken code too, because the replay merges
the two modules in the order that happens to be safe. Doppler's apply merged
them in the other order. The writer-order test runs both orders and is the
one that fails without the fix.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run
from just_makeit._object import _merge_module_init

ENUM = "ENG_STATE"

SHARED_TOML = """
[[enum]]
name = "eng_state"
choices = ["searching", "tracking", "lost"]

[module.wfm]
objects = ["synth"]

[module.wfm.reexports]
wfm_reader = ["Reader"]

[synth]
class_name = "Synth"
no_step = "true"

[module.wfm_reader]
objects = ["wfm_reader"]
package = "wfm"

[wfm_reader]
class_name = "Reader"
no_step = "true"

[[wfm_reader.state]]
name = "mode"
type = "int"
default = "0"
"""


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _init_bindings(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """``{submodule: names}`` for each ``from .X import`` line, and
    ``__all__``, read without executing the file."""
    tree = ast.parse(
        (root / "src" / "probe" / "wfm" / "__init__.py").read_text(
            encoding="utf-8"
        )
    )
    lines: dict[str, list[str]] = {}
    exported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            lines.setdefault(node.module or "", []).extend(
                a.name for a in node.names
            )
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            exported = [ast.literal_eval(e) for e in node.value.elts]
    return lines, exported


def _doppler_shape(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    _silent(new_run, "probe", root)
    manifest = root / C.FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + SHARED_TOML, encoding="utf-8"
    )
    _silent(apply_run, root)
    r = run_cli(
        "property",
        "wfm_reader",
        "mode",
        "--module",
        "wfm_reader",
        "--type",
        "int",
        "--enum",
        "eng_state",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    _silent(apply_run, root)
    return root


def test_every_all_name_is_bound_by_an_import_line(tmp_path):
    root = _doppler_shape(tmp_path)
    lines, exported = _init_bindings(root)
    bound = {n for names in lines.values() for n in names}
    assert ENUM in exported, exported
    assert not set(exported) - bound, (set(exported) - bound, lines)


def test_the_sibling_enum_joins_the_siblings_line(tmp_path):
    """The re-export `wfm_reader = ["Reader"]` keeps Reader and does not cut
    the line the sibling owns back to it."""
    root = _doppler_shape(tmp_path)
    lines, _ = _init_bindings(root)
    assert lines["wfm_reader"].count("Reader") == 1, lines
    assert ENUM in lines["wfm_reader"], lines


def test_a_second_apply_and_status_agree(tmp_path):
    root = _doppler_shape(tmp_path)
    before = _init_bindings(root)
    _silent(apply_run, root)
    assert _init_bindings(root) == before
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_either_writer_order_keeps_the_invariant():
    """The two merges run in a project-defined order; neither may leave a
    name in __all__ that no line binds."""
    base = '__all__ = ["Synth"]\n'
    reader = ["Reader", ENUM]

    def wfm(text):
        return _merge_module_init(
            text,
            "wfm",
            ["Synth"],
            {"wfm_reader": ["Reader"]},
            siblings=["wfm_reader"],
        )

    def wfm_reader(text):
        return _merge_module_init(text, "wfm_reader", reader, siblings=["wfm"])

    for first, second in ((wfm, wfm_reader), (wfm_reader, wfm)):
        text = second(first(second(first(base))))
        tree = ast.parse(text)
        bound = {
            a.name
            for n in tree.body
            if isinstance(n, ast.ImportFrom)
            for a in n.names
        }
        (all_node,) = [
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "__all__" for t in n.targets)
        ]
        exported = {ast.literal_eval(e) for e in all_node.value.elts}
        assert exported <= bound, (exported - bound, text)
        assert ENUM in bound, text
