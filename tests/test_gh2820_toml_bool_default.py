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

import pytest
from _jmrun import run_cli

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


def _project(tmp_path: Path, kind: str, ty: str, value: str) -> Path:
    """A project whose object `o` has one row of *kind* with the TOML
    value *value* (unquoted text, so `true` is a TOML bool)."""
    tmp_path.mkdir(exist_ok=True)
    r = run_cli("new", "p", "--object", "o", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    row = _ROWS[kind].format(ty=ty) + f"default = {value}\n"
    frag.write_text(text + "\n" + row, encoding="utf-8")
    return root


@pytest.mark.parametrize("kind", sorted(_ROWS))
@pytest.mark.parametrize("value", ["true", "false"])
def test_a_bool_default_is_accepted_on_a_bool_row(tmp_path, kind, value):
    """`default = true` on a bool row applies, and is the C `true`."""
    root = _project(tmp_path, kind, "bool", value)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    # The oracle is the same project written the way jm writes it: the
    # bool respelled `"true"` must render every generated file identically.
    twin = _project(tmp_path / "twin", kind, "bool", f'"{value}"')
    run_cli("apply", cwd=twin)
    assert _tree(root) == _tree(twin)


def _tree(root: Path) -> "dict[str, str]":
    """Every generated source file under *root*, by relative path."""
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for sub in ("native", "src")
        for p in sorted((root / sub).rglob("*"))
        if p.suffix in {".c", ".h", ".pyi", ".py", ".txt"}
    }


@pytest.mark.parametrize("kind", sorted(_ROWS))
@pytest.mark.parametrize(
    ("ty", "value"),
    [("int", "5"), ("double", "1.5"), ("int", "true"), ("bool", "[1]")],
)
def test_any_other_non_string_default_is_one_refusal_line(
    tmp_path, kind, ty, value
):
    """A number, a list, or a bool on a non-bool row is refused in one
    `error:` line naming the row -- through `apply` and `status --check`,
    never a traceback."""
    root = _project(tmp_path, kind, ty, value)
    for cmd in (("apply",), ("status", "--check")):
        r = run_cli(*cmd, cwd=root)
        assert r.returncode != 0, cmd
        assert "Traceback" not in r.stderr, r.stderr
        assert "default = " in r.stderr and "w]" in r.stderr, r.stderr
        assert "must be a string" in r.stderr, r.stderr
