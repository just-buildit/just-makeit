"""`_dump` round-trips custom C bodies (create_impl / reset_impl / …).

A C.load/C.save round-trip on a fragment that carries hand-written heredoc
bodies must preserve them — and re-parse them onto the component, not the last
sub-table entry (TOML ordering). Before this, re-saving a manifest silently
dropped create_impl/reset_impl/destroy_impl/impl, so any project that set
[project] keys via C.save after writing such a fragment lost its C bodies.
Surfaced by the kitchen_sink integration example.
"""

import sys
import tomllib
from pathlib import Path

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
