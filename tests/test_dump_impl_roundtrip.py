"""`_dump` round-trips custom C bodies (create_impl / reset_impl / …).

A C.load/C.save round-trip on a fragment that carries hand-written heredoc
bodies must preserve them — and re-parse them onto the component, not the last
sub-table entry (TOML ordering). Before this, re-saving a manifest silently
dropped create_impl/reset_impl/destroy_impl/impl, so any project that set
[project] keys via C.save after writing such a fragment lost its C bodies.
Surfaced by the kitchen_sink integration example.
"""

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402


def test_dump_preserves_impl_bodies_onto_component():
    cfg = {
        "obj": {
            "arg_type": "float",
            "return_type": "float",
            "impl": "return x * 2.0f;",
            "create_impl": "obj->h = dep_create();\nif (!obj->h) return NULL;",
            "reset_impl": "state->h = NULL;",
            "destroy_impl": "dep_destroy(state->h);",
            "state": [{"name": "h", "type": "dep_t *", "opaque": True}],
        }
    }
    dumped = C._dump(cfg)
    rt = tomllib.loads(dumped)["obj"]
    for key in ("impl", "create_impl", "reset_impl", "destroy_impl"):
        assert key in rt, f"{key} lost on round-trip"
    # bodies land on the component, not the [[obj.state]] entry
    assert "dep_create" in rt["create_impl"]
    assert "h" not in rt["state"][0].get("create_impl", "")
    # heredocs precede the sub-table (else TOML mis-parses them)
    assert dumped.index("create_impl") < dumped.index("[[obj.state]]")


def test_dump_omits_absent_impl_bodies():
    cfg = {"obj": {"arg_type": "float", "return_type": "float"}}
    dumped = C._dump(cfg)
    assert "create_impl" not in dumped
    assert "impl" not in dumped


def test_dump_impl_is_idempotent_across_resaves():
    """gh-192: a load->dump->load->dump cycle must not grow the impl body.

    TOML keeps the trailing newline from `impl = \"\"\"\\n{body}\\n\"\"\"`; without
    stripping it the body accumulates a blank line on every C.save, which made
    a fresh `jm apply` perpetually 'stale' (the generated step gained a blank
    line each reconcile)."""
    cfg = {
        "obj": {
            "arg_type": "void",
            "return_type": "float",
            "impl": "return 1.0f;",
            "create_impl": "obj->x = 0;",
        }
    }
    d1 = C._dump(cfg)
    d2 = C._dump(tomllib.loads(d1))
    d3 = C._dump(tomllib.loads(d2))
    assert d1 == d2 == d3, "dump is not idempotent — impl body is growing"
    body = tomllib.loads(d3)["obj"]["impl"]
    assert body.strip("\n") == "return 1.0f;"
