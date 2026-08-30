"""gh-1200: a `--no-state` object's header shipped an internal placeholder.

``jm object nos --no-state`` wrote ``native/inc/nos/nos_core.h`` containing

    typedef struct {
        /* <<IMPLEMENT: add fields >> */
    /*<<property_struct_fields>>*/
    } nos_state_t;

An object *with* state was unaffected, which is the interesting part: the same
function renders both.

**The cause was not "the later pass never runs".** The slot is set on the
no-state path (``_context/_state.py``'s ``no_state`` branch sets it to ``""``
like every other default). It was consumed too early. ``render`` substituted
ctx keys sequentially in **insertion order**, and ``state_struct_decl``'s value
carries ``/*<<property_struct_fields>>*/`` nested inside it so the properties
pass can land fields within the braces. So the nested token was filled only
when the decl happened to be inserted *before* the slot — and the two branches
of ``make_state_ctx`` insert them in opposite orders:

===============  ==========================  ============
branch           insertion order             result
===============  ==========================  ============
stateful         decl, then slot             filled
``--no-state``   slot, then decl             left in file
===============  ==========================  ============

Dict ordering is not a property anything can see, which is how this survived in
one function with both orders in it. ``render`` now sweeps to a fixed point, so
the question does not arise.

The checks below assert the **artifact** — the header jm actually wrote — for
both shapes, because asserting the ctx dict would have passed throughout: the
key was always there with the right value.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._init import run as init_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._render import render, unfilled_slots  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run(
            "p",
            proj,
            object_names=[],
            state_vars=[],
            arg_type="float",
            return_type="float",
            pytest_=False,
            pytest_benchmark_=False,
        )
    return proj


def _headers(proj: Path) -> list[Path]:
    return sorted((proj / "native" / "inc").rglob("*_core.h"))


def test_no_generated_header_carries_an_unfilled_slot(tmp_path: Path):
    """Every header, both shapes, in either form of the placeholder.

    Registration-free: it walks the headers jm wrote rather than naming the
    `--no-state` one, so a third shape is covered without being added here.
    """
    proj = _project(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        init_run(proj, "nos", no_state=True)
        init_run(proj, "yes", state_vars=[("gain", "float", "1.0f")])
        init_run(proj, "prop", state_vars=[("gain", "float", "1.0f")])
        # `field=True` is what makes a property add a struct field, which
        # is the case that USES the nested slot -- a property backed by an
        # existing state var would leave it empty and prove nothing.
        property_run(
            proj,
            "prop",
            "level",
            module=None,
            ctype="float",
            writable=True,
            field=True,
        )

    headers = _headers(proj)
    assert len(headers) >= 3, f"fixture lost a shape: {headers}"

    leftovers = {
        h.name: sorted(unfilled_slots(h.read_text(encoding="utf-8")))
        for h in headers
        if unfilled_slots(h.read_text(encoding="utf-8"))
    }
    assert not leftovers, f"generated header(s) carry a slot: {leftovers}"


def test_the_literal_token_is_absent_from_the_no_state_header(
    tmp_path: Path,
):
    """Named directly, since `unfilled_slots` is itself under test above.

    The `<<IMPLEMENT: add fields >>` marker beside it is deliberate output and
    must survive — a fix that stripped every `<<...>>` from the header would
    satisfy the sweep and delete the author's TODO.
    """
    proj = _project(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        init_run(proj, "nos", no_state=True)
    text = (proj / "native" / "inc" / "nos" / "nos_core.h").read_text(
        encoding="utf-8"
    )
    assert "property_struct_fields" not in text
    assert "IMPLEMENT: add fields" in text


def test_render_fills_a_nested_slot_in_either_ctx_order():
    """The mechanism, pinned directly.

    Both dicts hold the same keys and values; only insertion order differs.
    Before gh-1200 the second one left the token in the output, and that is
    the entire difference between the two branches of `make_state_ctx`.
    """
    nested = (
        "typedef struct {\n    int a;\n/*<<property_struct_fields>>*/\n} t;"
    )
    decl_first = {
        "state_struct_decl": nested,
        "property_struct_fields": "",
    }
    slot_first = {
        "property_struct_fields": "",
        "state_struct_decl": nested,
    }
    for ctx in (decl_first, slot_first):
        out = render("<<state_struct_decl>>", ctx)
        assert "property_struct_fields" not in out, out


def test_render_terminates_on_a_self_referential_slot():
    """The sweep is bounded, so a value naming its own key stops rather than
    spins. Nothing real does this; the bound is what makes that safe."""
    assert render("<<x>>", {"x": "<<x>>"}) == "<<x>>"
