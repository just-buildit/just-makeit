"""gh-2401: a view runs ``init_post_parse`` exactly when its parse is its own
or its parent's.

``init_post_parse`` completes the constructor's argument parse (doppler's
``PN``: ``poly == 0`` selects the maximal-length polynomial). A view
(gh-504) built none, whatever it declared: `_object._make_view_ctx` never
passed one, so a view constructed over the parent's arguments skipped the
step that gives them meaning.

The rule (`_config.view_init_post_parse`): a view that inherits its
parent's ``init_params`` inherits its ``init_post_parse`` -- the same parse,
completed the same way. A view that declares its own ``init_params`` parses
different arguments, which the parent's snippet may not even name, so it
runs only the ``init_post_parse`` it declares itself.

GATE: one module object with a snippet and three views -- inheriting,
declaring their own params, declaring their own params AND snippet -- and
each view's binding fragment carries exactly the snippet the rule gives it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

PARENT = "    if (poly == 0)\n        poly = PARENT_SNIPPET (length);\n"
OWN = "    if (seed == 0)\n        seed = OWN_SNIPPET ();\n"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


@pytest.fixture(scope="module")
def frags(tmp_path_factory: pytest.TempPathFactory) -> "dict[str, str]":
    root = tmp_path_factory.mktemp("gh2401view")
    _ok("new", "q", "--no-c-prefix", cwd=root)
    proj = root / "q"
    _ok("module", "dsp", cwd=proj)
    _ok(
        "object", "pn", "--module", "dsp", "--no-state",
        "--init-param", "poly:uint64_t:0",
        "--init-param", "length:uint32_t:0", cwd=proj,
    )  # fmt: skip
    views = ("Same", "Other", "OtherOwn")
    for view in views:
        _ok(
            "view", "pn", view, "--module", "dsp",
            "--create-fn", f"pn_create_{view.lower()}", cwd=proj,
        )  # fmt: skip
    cfg = C.load(proj)
    cfg["pn"]["init_post_parse"] = PARENT
    seed = [{"name": "seed", "type": "uint32_t", "default": "0"}]
    for row in cfg["pn"]["views"]:
        if row["class_name"] == "Other":
            row["init_params"] = seed
        if row["class_name"] == "OtherOwn":
            row["init_params"] = seed
            row["init_post_parse"] = OWN
    C.save(proj, cfg)
    # A fragment is sacred: delete each so `apply` renders it from the
    # manifest, which is the render under test.
    src = proj / "native" / "src" / "dsp"
    for frag in ("pn", *(v.lower() for v in views)):
        (src / f"dsp_ext_{frag}.c").unlink()
    _ok("apply", cwd=proj)
    return {
        f: (src / f"dsp_ext_{f}.c").read_text(encoding="utf-8")
        for f in ("pn", *(v.lower() for v in views))
    }


def test_the_parent_runs_its_snippet(frags: "dict[str, str]") -> None:
    assert "PARENT_SNIPPET" in frags["pn"]


def test_a_view_inheriting_the_parse_runs_the_parents_snippet(
    frags: "dict[str, str]",
) -> None:
    text = frags["same"]
    assert "PARENT_SNIPPET" in text, text
    created = text.index("pn_create_same")
    assert text.index("PARENT_SNIPPET") < created


def test_a_view_with_its_own_params_does_not_inherit_it(
    frags: "dict[str, str]",
) -> None:
    assert "PARENT_SNIPPET" not in frags["other"], frags["other"]


def test_a_view_with_its_own_params_runs_its_own_snippet(
    frags: "dict[str, str]",
) -> None:
    text = frags["otherown"]
    assert "OWN_SNIPPET" in text, text
    assert "PARENT_SNIPPET" not in text


def test_the_replay_keeps_every_view_row_whole(tmp_path: Path) -> None:
    """The replay rebuilds each view from its row, key by key; a key it does
    not forward is a key every render of the replayed manifest lacks -- the
    view's own snippet was one. Each row must come back as written.
    Reached through the Python API: `replay_out` is `status`'s internal
    handle on the replay (gh-949), with no flag."""
    import contextlib
    import io

    from just_makeit import _apply

    _ok("new", "q", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "q"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "pn", "--module", "dsp", "--init-param", "n:int:0", cwd=proj)
    _ok(
        "view", "pn", "Own", "--module", "dsp",
        "--create-fn", "pn_create_own", cwd=proj,
    )  # fmt: skip
    cfg = C.load(proj)
    (row,) = cfg["pn"]["views"]
    row["init_params"] = [{"name": "m", "type": "int", "default": "0"}]
    row["init_post_parse"] = "    m += 1;\n"
    C.save(proj, cfg)
    out = tmp_path / "replay"
    with contextlib.redirect_stdout(io.StringIO()):
        _apply.run(proj, replay_out=out)
    assert C.load(out)["pn"]["views"] == C.load(proj)["pn"]["views"]
