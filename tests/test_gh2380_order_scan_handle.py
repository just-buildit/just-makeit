"""gh-2380: scan the handle kind's whole generated surface.

Renders handle modules covering every method shape `_handle` has (a)-(f),
`nogil`, `status_return`, `error`, path / string / enum / scalar args, the
getter forms and a writable property, and reads the C jm generated for any
function that checks `closed`, converts an argument and then calls the
backing without checking again (`_order_scan`). The handle's `Slow.feed`
compiled probe is in `test_gh2380_handle_compiled`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _order_scan import unrechecked

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

import test_handle_build as TH  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402


def _wide_module() -> dict:
    """The shapes `test_handle_build`'s ring does not have."""
    return {
        "kind": "handle",
        "backing": "wide",
        "header": "wide/wide.h",
        "type_name": "Wide",
        "create_fn": "wide_open",
        "close_fn": "wide_close",
        "create_args": [{"name": "k", "type": "int"}],
        "methods": [
            {  # (e) a string arg -> an array sized by the handle
                "name": "render",
                "fn": "wide_render",
                "out_len_fn": "wide_len",
                "returns": "float[]",
                "args": [{"name": "spec", "type": "string"}],
            },
            {  # (f) scalar args -> handle-length bytes
                "name": "blob",
                "fn": "wide_blob",
                "out_len_fn": "wide_blob_len",
                "returns": "bytes",
                "args": [{"name": "k", "type": "int"}],
            },
            {  # (c) a count, then more args behind it
                "name": "readn",
                "fn": "wide_readn",
                "returns": "float[]",
                "args": [
                    {"name": "n", "type": "size_t"},
                    {"name": "gain", "type": "float", "default": "1.0f"},
                ],
            },
            {  # (b) array-in, no GIL
                "name": "feed",
                "fn": "wide_feed",
                "returns": "int",
                "nogil": True,
                "args": [{"name": "x", "type": "float[]"}],
            },
            {  # (b) array-in whose int return is a status
                "name": "feed_st",
                "fn": "wide_feed_st",
                "returns": "int",
                "status_return": True,
                "args": [{"name": "x", "type": "float[]"}],
            },
            {  # (a) an enum arg
                "name": "setmode",
                "fn": "wide_setmode",
                "args": [{"name": "m", "type": "int", "enum": "mode"}],
            },
            {  # (a) a string arg, and a nogil scalar call
                "name": "label",
                "fn": "wide_label",
                "nogil": True,
                "args": [{"name": "s", "type": "string"}],
            },
            {  # (a) no args at all
                "name": "poke",
                "fn": "wide_poke",
            },
        ],
        "getters": [
            {
                "fn": "wide_get_level",
                "out": "float",
                "fields": [
                    {
                        "name": "level",
                        "type": "float",
                        "writable_fn": "wide_set_level",
                    },
                ],
            },
            {
                "fn": "wide_get_mode",
                "out": "int",
                "fields": [
                    {
                        "name": "mode",
                        "type": "int",
                        "enum": "mode",
                        "writable_fn": "wide_set_mode",
                    },
                ],
            },
        ],
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("handle_scan")
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg["enum"] = [{"name": "mode", "values": ["a", "b"]}]
    mods = cfg.setdefault("module", {})
    for name, fn in (
        ("ring", TH._ringbuf_module),
        ("ticks", TH._ticks_module),
        ("flaky", TH._flaky_module),
        ("wide", _wide_module),
    ):
        mods[name] = TH._placed(fn(), tmp)
    C.save(tmp, cfg)
    apply_run(tmp)
    return tmp


def _flagged(root: Path) -> "dict[str, list[str]]":
    out = {}
    for c in sorted((root / "native" / "src").rglob("*_ext*.c")):
        hits = unrechecked(c.read_text(encoding="utf-8"))
        if hits:
            out[c.name] = [n for n, _ in hits]
    return out


#: Functions known to have the window, by file. It may only shrink.
BASELINE: "dict[str, list[str]]" = {}


def test_the_fixture_rendered_every_module(project):
    for name in ("ring", "ticks", "flaky", "wide"):
        text = (project / "native/src" / name / f"{name}_ext.c").read_text()
        assert "closed" in text, name
    wide = (project / "native/src/wide/wide_ext.c").read_text()
    for fn in ("Wide_render", "Wide_blob", "Wide_readn", "Wide_feed"):
        assert fn in wide, fn


def test_handle_kind_has_no_unrechecked_conversion(project):
    got = _flagged(project)
    assert got == BASELINE, (
        "a handle binding function checks `closed`, converts an argument "
        "(which can run Python and close the handle) and then calls the "
        f"backing with no recheck (gh-2380): {got}"
    )
