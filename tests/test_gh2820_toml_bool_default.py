"""gh-2820 -- a TOML bool ``default`` is a bool field's default, or refused.

A manifest default is C source text, written as a string
(``default = "false"``). ``default = true`` is valid TOML and the natural
thing to write by hand for a ``bool`` field, but ``tomllib`` hands it over
as a Python ``bool`` and the readers called ``.strip()`` on it: ``apply``
died with an ``AttributeError`` traceback. A number or a list did the same.

Now ``_keys.default_errors``, asked once at ``_config.load`` over every row
that carries a ``default``, respells a bool on a ``bool`` row to
``"true"`` / ``"false"`` (never ``str(True)``, which gh-2137 refuses) and
refuses every other non-string in one line naming the row.

The cases are the three row kinds a bool default reaches -- a state field,
an init param and a method param -- driven through ``apply`` and ``status
--check``, and the refusals through the same two commands.
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest
from _jmrun import run_cli

from just_makeit._types import default_type_error

#: (table header the row sits under, the row's other keys). Each is
#: appended to the object's fragment as a row with `default = <value>`.
_ROWS = {
    "state": '[[o.state]]\nname = "w"\ntype = "{ty}"\n',
    "init_param": '[[o.init_params]]\nname = "w"\ntype = "{ty}"\n',
    "method_param": (
        '[[o.methods]]\nname = "ctl"\narg_type = "void"\n'
        'return_type = "float"\n\n'
        '[[o.methods.params]]\nname = "w"\ntype = "{ty}"\n'
    ),
}


def _tree(root: Path) -> "dict[str, str]":
    """Every file `apply` may write under *root*, by relative path."""
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for sub in ("native", "src", "objects")
        for p in sorted((root / sub).rglob("*"))
        if p.suffix in {".c", ".h", ".pyi", ".py", ".txt", ".toml"}
    }


@pytest.fixture(scope="module")
def _module_project(tmp_path_factory):
    """ONE project, applied; each case rewrites `objects/o.toml` in place
    and puts it back (a `jm new` per case cost two minutes)."""
    tmp = tmp_path_factory.mktemp("gh2820")
    r = run_cli("new", "p", "--object", "o", cwd=tmp)
    assert r.returncode == 0, r.stderr
    root = tmp / "p"
    frag = root / "objects" / "o.toml"
    return root, frag, frag.read_text(encoding="utf-8")


@pytest.fixture
def project(_module_project):
    """The module's project, put back as it was after EVERY case -- also a
    failing one, so a red case cannot leave a rewritten fragment behind
    for the next to trip over and hide the real failure."""
    yield _module_project
    root, frag, base = _module_project
    frag.write_text(base, encoding="utf-8")
    run_cli("apply", cwd=root)


def _set(project, kind: str, ty: str, value: str) -> None:
    """Write the fragment with one row of *kind* defaulting to *value*
    (unquoted text, so `true` is a TOML bool)."""
    _root, frag, base = project
    row = _ROWS[kind].format(ty=ty) + f"default = {value}\n"
    frag.write_text(base + "\n" + row, encoding="utf-8")


@pytest.mark.parametrize("kind", sorted(_ROWS))
@pytest.mark.parametrize("value", ["true", "false"])
def test_a_bool_default_is_accepted_on_a_bool_row(project, kind, value):
    """`default = true` on a bool row applies, and renders every generated
    file as the same row written the way jm writes it (`"true"`)."""
    root = project[0]
    _set(project, kind, "bool", value)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    got = _tree(root)
    # The manifest text differs by design; the oracle is what it renders.
    got.pop("objects/o.toml")
    _set(project, kind, "bool", f'"{value}"')
    assert run_cli("apply", cwd=root).returncode == 0
    want = _tree(root)
    want.pop("objects/o.toml")
    assert got == want


@pytest.mark.parametrize("kind", sorted(_ROWS))
@pytest.mark.parametrize(
    ("ty", "value"),
    [
        ("int", "5"),
        ("double", "1.5"),
        ("int", "true"),
        ("int", "false"),
        ("double", "true"),
        ("const char *", "true"),
        ("bool", "[1]"),
    ],
)
def test_any_other_non_string_default_is_one_refusal_line(
    project, kind, ty, value
):
    """A number, a list, or a bool on a non-bool row is refused in one
    `error:` line naming the row -- through `apply` and `status --check`,
    never a traceback -- and `apply` writes nothing."""
    root = project[0]
    _set(project, kind, ty, value)
    before = _tree(root)
    for cmd in (("apply",), ("status", "--check")):
        r = run_cli(*cmd, cwd=root)
        assert r.returncode != 0, cmd
        assert "Traceback" not in r.stderr, r.stderr
        assert "default = " in r.stderr and "w]" in r.stderr, r.stderr
        assert "must be a string" in r.stderr, r.stderr
    assert _tree(root) == before
    # The advice must never name a spelling jm refuses: the string the
    # message suggests, parsed out of it, is a valid default for the row's
    # own type.
    suggested = re.search(r'`default = "([^"]*)"`', r.stderr)
    assert suggested, r.stderr
    if suggested.group(1) != "...":
        assert default_type_error(ty, suggested.group(1)) == "", r.stderr
