"""gh-1517: every table a kind module accepts is honoured, or it is refused.

Measured on main (a real `jm apply`, one `zz*` row per table, a grep of every
generated file): six tables validated through `_keys` and generated nothing,
and a seventh made `apply` exit 1 with advice that would have created a second,
conflicting module.

| kind     | table         | before                                        |
| -------- | ------------- | --------------------------------------------- |
| handle   | `properties`  | accepted, rendered nowhere                    |
| handle   | `init_params` | accepted, rendered nowhere                    |
| capsule  | `getters`     | accepted, rendered nowhere                    |
| composer | `getters`     | accepted, rendered nowhere                    |
| composer | `properties`  | accepted, rendered nowhere                    |
| composer | `init_params` | accepted, rendered nowhere                    |
| all 3    | `functions`   | `apply` exits 1: "module 'X' not found. Run   |
|          |               | 'just-makeit module X' first."                |

The first six are gh-816's shape with the registry supplying the silence:
`_keys` validated the ROWS, so a typo inside one was reported, and nothing read
the table, so a correct row vanished. Each is now out of its kind's accepted
set, and the warning names the table that face does read.

The gate is registration-free in the direction that matters: it walks
`_keys.KIND_TABLE_VOCAB`, so a table added there must come with a sample row
that renders, or this fails naming it.
"""

from __future__ import annotations

import contextlib
import copy
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from _jmrun import run_cli  # noqa: E402
from test_capsule_apply import _capsule_module  # noqa: E402
from test_composer_apply import _composer_module  # noqa: E402
from test_handle_apply import _ring_module  # noqa: E402

#: One working module per kind, from the suites that already build them.
KINDS = {
    "handle": ("ringbuf", _ring_module, []),
    "capsule": ("ddc_fn", _capsule_module, []),
    "composer": (
        "wfm_compose",
        _composer_module,
        [{"name": "wfm_type", "values": ["tone", "noise", "pn"]}],
    ),
}

#: A row per accepted table, carrying a `zz` marker the output must contain.
#: Keyed by (kind, table) where the faces disagree about a row's shape.
SAMPLES = {
    ("handle", "methods"): {"name": "zzmeth", "fn": "ringbuf_zz"},
    ("capsule", "methods"): {"name": "zzmeth"},
    ("handle", "getters"): {
        "fn": "ringbuf_zzstats",
        "out": "ringbuf_zz_t",
        "fields": [{"name": "zzfield", "type": "double"}],
    },
    ("handle", "factories"): {"name": "zzfactory", "create_fn": "ringbuf_zz"},
    ("handle", "create_args"): {"name": "zzarg", "type": "double"},
    ("handle", "create_post"): {"fn": "ringbuf_zzpost"},
    ("capsule", "properties"): {"name": "zzprop", "type": "double"},
    ("capsule", "init_params"): {"name": "zzinit", "type": "double"},
    ("composer", "extra_methods"): {"name": "zzextra", "fn": "Composer_zz"},
    ("composer", "serializers"): {"name": "zzser", "fn": "wfm_zz_ser"},
    ("composer", "settings"): {
        "name": "zzset",
        "type": "int",
        "setter_fn": "wfm_compose_set_zz",
        "getter_fn": "wfm_compose_get_zz",
    },
    ("handle", "depends_on"): {"name": "zzdep", "link": True},
    ("capsule", "depends_on"): {"name": "zzdep", "link": True},
    ("composer", "depends_on"): {"name": "zzdep", "link": True},
}

#: The tables refused on a face, and the words each warning must carry so the
#: author learns which table that face DOES read.
REFUSED = {
    ("handle", "properties"): "getters",
    ("handle", "init_params"): "create_args",
    ("capsule", "getters"): "properties",
    ("composer", "getters"): "computed",
    ("composer", "properties"): "computed",
    ("composer", "init_params"): "settings",
    ("handle", "functions"): "plain module",
    ("capsule", "functions"): "plain module",
    ("composer", "functions"): "plain module",
}

_ROW_FOR_REFUSED = {
    "properties": {"name": "zzprop", "type": "double"},
    "init_params": {"name": "zzinit", "type": "double"},
    "getters": {
        "fn": "zzget_fn",
        "out": "zzget_t",
        "fields": [{"name": "zzfield", "type": "double"}],
    },
    "functions": {"name": "zzfunc", "return_type": "double"},
}


def _project(root: Path, kind: str, table: str, row: dict) -> None:
    mod, factory, enums = KINDS[kind]
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("enum", []).extend(copy.deepcopy(enums))
    m = factory()
    m[table] = list(m.get(table) or []) + [copy.deepcopy(row)]
    cfg.setdefault("module", {})[mod] = m
    C.save(root, cfg)


def _apply(root: Path) -> str:
    out = io.StringIO()
    _keys._SEEN.clear()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        apply_run(root)
    return out.getvalue()


def _generated_text(root: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in root.rglob("*")
        if p.is_file()
        and (p.suffix in (".c", ".h", ".pyi") or p.name == "CMakeLists.txt")
    )


_ACCEPTED = sorted(
    (kind, table)
    for (kind, table) in _keys.KIND_TABLE_VOCAB
    if table in _keys._accepted_keys(kind)
)


@pytest.mark.parametrize("kind, table", _ACCEPTED)
def test_an_accepted_table_is_rendered(kind, table, tmp_path) -> None:
    """A row in an accepted table must reach the generated output.

    A table with no sample row fails here rather than being skipped: adding a
    table to `KIND_TABLE_VOCAB` must not be a way back into the silence.
    """
    row = SAMPLES.get((kind, table))
    assert row is not None, (
        f"{kind} module accepts `{table}`, and no sample row proves a row "
        f"in it is rendered: add one to SAMPLES, or refuse the table"
    )
    _project(tmp_path, kind, table, row)
    _apply(tmp_path)
    marker = next(v for v in row.values() if isinstance(v, str) and "zz" in v)
    assert marker in _generated_text(tmp_path), (
        f"`[[module.X.{table}]]` on a {kind} module was accepted and "
        f"rendered nowhere"
    )


@pytest.mark.parametrize("kind, table", sorted(REFUSED))
def test_a_refused_table_says_what_to_use(kind, table, tmp_path) -> None:
    """Refused, not dropped: apply completes, warns once, and names the
    table this face reads instead."""
    _project(tmp_path, kind, table, _ROW_FOR_REFUSED[table])
    said = _apply(tmp_path)
    lines = [ln for ln in said.splitlines() if f"`{table}`" in ln]
    assert lines, said
    assert REFUSED[(kind, table)] in lines[0], lines[0]
    assert "not found" not in said, said


@pytest.mark.parametrize("kind, table", sorted(REFUSED))
def test_a_refused_table_is_not_accepted(kind, table) -> None:
    assert table not in _keys._accepted_keys(kind)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_jm_function_on_a_kind_module_says_why(kind, tmp_path) -> None:
    """The CLI half. A kind module is not in `modules()`, so `jm function`
    reported it "not found" and advised `jm module <same name>` -- a second,
    conflicting module. It is found; it is the wrong kind."""
    mod = KINDS[kind][0]
    _project(tmp_path, kind, "depends_on", {"name": "widget", "link": True})
    r = run_cli("function", "zzfunc", "--module", mod, cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f'`kind = "{kind}"` module' in r.stderr, r.stderr
    assert "not found" not in r.stderr, r.stderr
    assert "zzfunc" not in (tmp_path / "just-makeit.toml").read_text(
        encoding="utf-8"
    )


def test_every_vocabulary_is_for_an_accepted_table() -> None:
    """Row vocabulary for a table the face refuses is dead weight: its rows
    are validated and then ignored, which reads as if they did something."""
    stale = [
        (k, t)
        for (k, t) in _keys.KIND_TABLE_VOCAB
        if t not in _keys._accepted_keys(k)
    ]
    assert stale == [], stale
