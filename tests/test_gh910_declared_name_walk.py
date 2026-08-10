"""Every name a manifest declares is walked, and every walked name is gated.

gh-910. `C.non_ascii_names` promised, in its own docstring, that it named
**every** declared name — "covering some kinds and not others breaks precisely
that [promise]... Partial coverage is worse than none, because it reads as a
clean bill of health." It was hand-written three times and each pass missed
kinds the next one added. The third pass still missed `array_args`, every
method `param`, a custom `destroy` name, and the whole handle/composer surface
(`create_args`, `getters.fields`, `methods.args`, `factories`, `serializers`).

Enforcement had the matching hole from the other side: `require_name` was
reachable from six of the eight kinds the report did cover, so `--state
gaïn:double:1.0` wrote `double gaïn;` into the sacred header and exited 0.

Both halves are one fix — derive the walk from the manifest, and put the check
where every manifest write already goes (`C.save`). The tests below are in two
groups matching that: the walk must find names nobody enumerated, and the gate
must be unreachable-around.

The interesting case throughout is a name that is *only* reachable this way.
Asserting that a method name is checked proves nothing — it was checked before
gh-910 by a different route.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._function import run as function_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _project(root, module=False):
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
        if module:
            module_run(root, "m")
        object_run(
            root,
            "w",
            "m" if module else None,
            arg_type="float",
            return_type="float",
        )
    return root


# --------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------


# Every one of these was invisible to the hand-written walk. They are listed
# as (manifest fragment, expected name) rather than asserted one function at a
# time so that adding a kind here is a one-line change — the point of gh-910
# being that the expensive part was ever having to remember them.
UNSEEN_BEFORE = [
    ("array arg", {"w": {"array_args": [{"name": "coëffs"}]}}, "coëffs"),
    (
        "method param",
        {"w": {"methods": [{"name": "go", "params": [{"name": "gaïn"}]}]}},
        "gaïn",
    ),
    (
        "result field",
        {
            "w": {
                "methods": [{"name": "go", "result_fields": [{"name": "idẍ"}]}]
            }
        },
        "idẍ",
    ),
    ("destroy method", {"w": {"destroy": {"name": "clöse"}}}, "clöse"),
    ("class name", {"w": {"class_name": "Wïdget"}}, "Wïdget"),
    (
        "function param",
        {
            "module": {
                "m": {
                    "functions": [
                        {"name": "design", "params": [{"name": "cutöff"}]}
                    ]
                }
            }
        },
        "cutöff",
    ),
    (
        "handle create arg",
        {"module": {"m": {"create_args": [{"name": "fïle"}]}}},
        "fïle",
    ),
    (
        "handle method arg",
        {
            "module": {
                "m": {"methods": [{"name": "read", "args": [{"name": "ñ"}]}]}
            }
        },
        "ñ",
    ),
    (
        "composer factory",
        {"module": {"m": {"factories": [{"name": "tône"}]}}},
        "tône",
    ),
    ("enum", {"enum": [{"name": "wfm_typé"}]}, "wfm_typé"),
    (
        "record name",
        {"w": {"methods": [{"name": "go", "record_name": "Hït"}]}},
        "Hït",
    ),
]


@pytest.mark.parametrize(
    "label,fragment,name",
    UNSEEN_BEFORE,
    ids=[u[0].replace(" ", "_") for u in UNSEEN_BEFORE],
)
def test_the_walk_finds_a_kind_nobody_enumerated(label, fragment, name):
    """Each of these was declared, reached generated C, and was unreported."""
    cfg = {"project": {"name": "p"}, **fragment}
    reported = [n for _, n in C.non_ascii_names(cfg)]
    assert name in reported, (
        f"a non-ASCII {label} is invisible to the report, so a project "
        f"renaming what `jm status` lists is still refused by `apply`: "
        f"{C.non_ascii_names(cfg)}"
    )


def test_a_capsule_name_is_not_held_to_an_identifier():
    """The one `*_name` key that is deliberately not one.

    Measured rather than assumed: doppler declares
    `capsule_name = "doppler.wfm.compose_state"`, a dotted PyCapsule string
    that reaches C as the literal handed to `PyCapsule_GetPointer`, never as a
    symbol. The walk is fail-closed on `*_name` keys, so this exception is the
    thing that has to be argued for — and if it is ever dropped, every project
    using a capsule stops saving.
    """
    cfg = {"project": {"name": "p"}, "w": {"capsule_name": "pkg.mod.thing"}}
    assert "pkg.mod.thing" not in [n for _, n, _ in C.declared_names(cfg)], (
        "a dotted capsule string is being treated as an identifier; that "
        "refuses every manifest declaring a capsule"
    )
    C.require_declared_names(cfg)  # must not raise


def test_a_dotted_module_id_is_validated_as_a_module_not_a_name():
    """`dsp.filters` is a legitimate declared name that is not an identifier.

    The first cut of the gate sent every walked name through `validate_name`
    and refused every nested-module project in the suite. Its own predicate
    exists; the gate dispatches to it.
    """
    cfg = {"project": {"name": "p"}, "module": {"dsp.filters": {}}}
    C.require_declared_names(cfg)  # must not raise
    assert ("module", "dsp.filters", "module.dsp.filters") in C.declared_names(
        cfg
    )


def test_the_report_says_where():
    """A path, because by the time the gate fires the manifest has hundreds.

    "'gaïn' is not a valid state field name" over a project with eleven
    objects is a search, not a diagnosis.
    """
    cfg = {"project": {"name": "p"}, "w": {"state": [{"name": "gaïn"}]}}
    kinds = {where for _, _, where in C.declared_names(cfg)}
    assert "w.state[0].name" in kinds, kinds


def test_an_app_name_is_left_alone():
    """`jm app --name my-tool` is a deliberate carve-out (gh-625).

    An app name becomes a filename, a CMake target and a console script's
    `prog`, all of which allow a hyphen. Nothing refuses it, so listing it
    under a report that says jm will would be false — and gating it would
    break every project with a hyphenated app.
    """
    cfg = {"project": {"name": "p"}, "app": {"name": "my-tool", "target": "t"}}
    assert "my-tool" not in [n for _, n, _ in C.declared_names(cfg)]
    C.require_declared_names(cfg)  # must not raise


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_save_refuses_a_name_no_command_would_have_caught(tmp_path):
    """The backstop, exercised through the shape that had no check at all.

    A state field is the kind gh-910 was filed for: `require_name` never saw
    it, so this must fail at `save` or nowhere.
    """
    root = _project(tmp_path / "p")
    cfg = C.load(root)
    cfg["w"]["state"] = [{"name": "gaïn", "type": "double", "default": "1.0"}]
    with pytest.raises(SystemExit):
        C.save(root, cfg)


def test_the_gate_fires_before_the_first_file_is_written(tmp_path):
    """Exit 1 is not enough — a half-made tree is the cost gh-625 named.

    `_init.run` composes the manifest section on its last line and writes the
    sacred header two hundred lines earlier, so the `save` gate alone let
    `double gaïn;` reach `w_core.h` before refusing. Recovery then means
    hand-editing a sacred file the author never wrote.
    """
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
    with contextlib.redirect_stderr(io.StringIO()):
        with pytest.raises(SystemExit):
            object_run(
                root,
                "w",
                None,
                arg_type="float",
                return_type="float",
                state_vars=[("gaïn", "double", "1.0")],
            )
    assert not (root / "native/inc/w/w_core.h").exists(), (
        "the object's sacred header was written before the name was refused, "
        "so the command failed and left a tree to clean up by hand"
    )


@pytest.mark.parametrize("bad", ["gaïn", "9bad", "has space"])
def test_a_method_param_never_reaches_generated_source(tmp_path, bad):
    """`jm method` renders the binding and the stub before it saves.

    Three spellings of invalid, because the non-ASCII one is the newest rule
    and the other two have been wrong since forever — a check that only fires
    on the newest is half a gate.
    """
    root = _project(tmp_path / "p")
    with contextlib.redirect_stderr(io.StringIO()):
        with contextlib.redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit):
                method_run(
                    root,
                    "w",
                    "go",
                    None,
                    "void",
                    "int",
                    False,
                    [],
                    params=[(bad, "double")],
                )
    written = "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in list(root.rglob("*.c"))
        + list(root.rglob("*.h"))
        + list(root.rglob("*.pyi"))
    )
    assert bad not in written, (
        f"'{bad}' reached generated source before the command refused it"
    )


def test_a_function_param_never_reaches_generated_source(tmp_path):
    """The peer of the method case, and it failed for the same reason.

    `_function.run` writes the module's C before saving too. Fixing one and
    not the other is the peer-implementation shape that has cost this
    predicate a whole round already.
    """
    root = _project(tmp_path / "p", module=True)
    with contextlib.redirect_stderr(io.StringIO()):
        with contextlib.redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit):
                function_run(root, "design", "m", params=[("gaïn", "double")])
    written = "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in list(root.rglob("*.c")) + list(root.rglob("*.h"))
    )
    assert "gaïn" not in written


def test_a_clean_project_still_saves(tmp_path):
    """The gate runs on every write, so the common path has to be untouched."""
    root = _project(tmp_path / "p")
    cfg = C.load(root)
    cfg["w"]["state"] = [{"name": "gain", "type": "double", "default": "1.0"}]
    C.save(root, cfg)
    assert C.state_vars(C.load(root), "w")[0][0] == "gain"
