"""gh-2112: a name declared twice in one C list is refused, not compiled.

Each of these lists becomes ONE C declaration -- the state struct,
``create()``'s parameters, a method's or function's parameters, a record's
fields -- so a name in it twice is a duplicate member or a redefined
parameter. jm accepted every one, wrote the tree, and ``make`` failed in the
compiler. Only ``jm add`` refused, and only its own new state names.

The check is ``_config.duplicate_name_errors``: asked by ``load`` and
``save``, so a hand-written manifest is refused by every command that reads
it, and up front by ``jm new`` and ``jm object``, so a refusal leaves no
half-made project and nothing to put back. Every case drives jm through
``run_cli`` and requires the tree to be byte-identical afterwards.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from _jmrun import run_cli

TWICE = "is declared twice"


def _snapshot(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha1(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    r = run_cli("new", "q", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "q"
    for args in (
        ("object", "s", "--state", "n:int:0"),
        ("module", "m"),
    ):
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    return root


def _refused(root: Path, *args: str, names: str) -> None:
    before = _snapshot(root)
    r = run_cli(*args, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert f"{names} {TWICE}" in r.stderr, r.stderr
    assert _snapshot(root) == before, "the refused command changed the tree"
    if args[0] == "object":
        # Refused before rendering, not undone after: `jm object` asks the
        # check up front, so there is nothing for gh-1867's undo to put back.
        assert "put back" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_jm_new_refuses_before_the_project_exists(tmp_path: Path) -> None:
    r = run_cli(
        "new",
        "p",
        "--object",
        "o",
        "--state",
        "g:double:1",
        "--state",
        "g:double:2",
        cwd=tmp_path,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "'o' state: 'g' is declared twice" in r.stderr, r.stderr
    assert not (tmp_path / "p").exists(), "a half-made project was left"


@pytest.mark.parametrize(
    "args, names",
    [
        (
            ("object", "o", "--state", "g:double:1", "--state", "g:double:2"),
            "'o' state: 'g'",
        ),
        (
            ("object", "k", "--init-param", "n:int:1", "--init-param", "n:int:2"),
            "'k' init_params: 'n'",
        ),
        (
            (
                "method", "s", "go", "--arg-type", "float[]", "--return-type",
                "double", "--param", "a:int", "--param", "a:int",
            ),
            "'s' method 'go' params: 'a'",
        ),
        (
            (
                "method", "s", "pk", "--arg-type", "float[]", "--return-type",
                "pk_t", "--result-field", "a:int", "--result-field", "a:int",
            ),
            "'s' method 'pk' result_fields: 'a'",
        ),
        (
            ("function", "f", "--module", "m", "--param", "a:int",
             "--param", "a:int"),
            "module 'm' function 'f' params: 'a'",
        ),
    ],
    ids=["state", "init-param", "method-param", "result-field", "fn-param"],
)  # fmt: skip
def test_every_verb_refuses_and_leaves_the_tree(proj, args, names) -> None:
    _refused(proj, *args, names=names)


def test_a_hand_written_duplicate_is_refused_by_apply(proj: Path) -> None:
    frag = proj / "objects" / "s.toml"
    text = frag.read_text(encoding="utf-8")
    row = text[text.index("[[s.state]]") :].split("\n\n")[0]
    assert 'name = "n"' in row, row
    frag.write_text(text + "\n" + row + "\n", encoding="utf-8")
    for verb in ("apply", "status"):
        r = run_cli(verb, cwd=proj)
        assert r.returncode == 1, (verb, r.stdout + r.stderr)
        assert "'s' state: 'n' is declared twice" in r.stderr, r.stderr


def test_a_state_field_and_its_init_param_may_share_a_name(proj) -> None:
    """The over-fix guard: an init param setting its field is one name."""
    r = run_cli(
        "object", "t", "--state", "n:int:0", "--init-param", "n:int:2",
        cwd=proj,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr


def _with_duplicate_state(proj: Path) -> None:
    frag = proj / "objects" / "s.toml"
    text = frag.read_text(encoding="utf-8")
    row = text[text.index("[[s.state]]") :].split("\n\n")[0]
    frag.write_text(text + "\n" + row + "\n", encoding="utf-8")


def test_load_refuses_it_on_its_own(proj: Path) -> None:
    """Each half, alone: every CLI path above reaches both `load` and `save`,
    so a CLI test stays green with either one removed."""
    from just_makeit import _config as C
    from just_makeit._report import Refusal

    _with_duplicate_state(proj)
    with pytest.raises(Refusal, match="'s' state: 'n' is declared twice"):
        C.load(proj)


def test_save_refuses_it_on_its_own(proj: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._report import Refusal

    cfg = C.load(proj)
    cfg["s"]["state"].append(dict(cfg["s"]["state"][0]))
    before = _snapshot(proj)
    with pytest.raises(Refusal, match="'s' state: 'n' is declared twice"):
        C.save(proj, cfg)
    assert _snapshot(proj) == before


def test_a_name_may_repeat_across_lists(proj: Path) -> None:
    """The over-reach guard: the check is per list, not per object or module.

    The same parameter name in two methods, or in two functions, is two C
    declarations, each with one ``a``.
    """
    for args in (
        ("method", "s", "go", "--arg-type", "float[]", "--return-type",
         "double", "--param", "a:int"),
        ("method", "s", "run", "--arg-type", "float[]", "--return-type",
         "double", "--param", "a:int"),
        ("function", "f", "--module", "m", "--param", "a:int"),
        ("function", "g", "--module", "m", "--param", "a:int"),
    ):  # fmt: skip
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("where", ["init_params", "method"])
def test_a_views_own_lists_are_checked(where: str) -> None:
    """A view (gh-504) has its own constructor and methods: one C list each."""
    from just_makeit import _config as C

    rows = [{"name": "n", "type": "int"}, {"name": "n", "type": "int"}]
    view: dict = {"class_name": "V", "create_fn": "o_create_v"}
    if where == "init_params":
        view["init_params"] = rows
        want = "'o' view 'V' init_params: 'n' is declared twice"
    else:
        view["methods"] = [{"name": "go", "params": rows}]
        want = "'o' view 'V' method 'go' params: 'n' is declared twice"
    errors = C.duplicate_name_errors({"o": {"views": [view]}})
    assert any(e.startswith(want) for e in errors), errors
