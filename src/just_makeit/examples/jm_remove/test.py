"""End-to-end test: `jm remove` deletes a declaration from both the manifest
and the generated bindings, across every removal surface it exposes.

Called by tests/test_examples.py as: run(root: Path) -> None

Exercises every removal surface exposed by `jm remove`:

  - ``remove method``   — drops a named execute variant from an object; verifies
                          the generated ext.c no longer contains the binding and
                          that the object itself is still present.
  - ``remove property`` — drops a PyGetSetDef entry from an object; verifies
                          the TOML property list and the regenerated ext.c.
  - ``remove function`` — drops a module-level C function binding; verifies the
                          ext.c aggregator and the TOML ``[module.synth]``
                          functions list.
  - ``remove state``    — removes one field from an object's state struct;
                          verifies the regenerated ``_core.h`` header drops the
                          field while preserving the others, and that the TOML
                          state list is updated.
  - ``remove object``   — deletes all generated files for a whole object, strips
                          it from the TOML and from the module membership list,
                          and confirms the sibling object is unaffected.

No cmake build is performed — all assertions are purely on TOML state and the
content of generated C/H files so the test completes in well under a second
even on constrained CI runners.

Project layout
--------------
  Project ``my_synth``, module ``synth``, objects ``osc`` and ``env``:

  osc (oscillator)
    arg_type="void", return_type="float _Complex", mutable=True
    state: phase (uint32_t), freq (float)
    method: tune(detune: float) -> void   (name avoids collision with the
                                           auto-generated set_freq state setter)

  env (envelope)
    arg_type="float", return_type="float"
    state: attack (float), decay (float), level (float)
    property: clipping (int32_t, read-only) — not backed by a state field

  module-level function: poly_detune(cents: float) -> float
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def run(root: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._function import run as jm_function
    from just_makeit._method import run as jm_method
    from just_makeit._module import run as jm_module
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._property import run as jm_property
    from just_makeit._remove import run as jm_remove

    # ── 1. Scaffold ───────────────────────────────────────────────────────────

    proj = root / "my_synth"
    jm_new("my_synth", proj)

    # Create the synth module that will hold both objects.
    jm_module(proj, "synth")

    # osc — void-arg oscillator, mutable so step() takes a self-pointer.
    jm_object(
        proj,
        "osc",
        module="synth",
        arg_type="void",
        return_type="float _Complex",
        mutable=True,
        state_vars=[
            ("phase", "uint32_t", "0"),
            ("freq", "float", "440.0f"),
        ],
    )

    # env — envelope follower with three state variables.
    jm_object(
        proj,
        "env",
        module="synth",
        arg_type="float",
        return_type="float",
        state_vars=[
            ("attack", "float", "0.01f"),
            ("decay", "float", "0.1f"),
            ("level", "float", "0.0f"),
        ],
    )

    # Add a method that applies a detune offset to the oscillator frequency.
    # Named "tune" (not "set_freq") to avoid colliding with the auto-generated
    # Osc_set_freq setter that the `freq` state variable produces — if both
    # exist the removal assertion cannot distinguish them.
    jm_method(
        proj,
        "osc",
        "tune",
        module="synth",
        arg_type="void",
        return_type="void",
        params=[("detune", "float")],
        variable_output=False,
        multi_output=[],
    )

    # Add a read-only property to env for an internally-managed clipping flag.
    # field=False (default) because "clipping" is not in the state struct — it
    # is a computed/internal value that the user exposes via osc_get_clipping().
    jm_property(proj, "env", "clipping", "synth", "int32_t", False)

    # Add a module-level helper function to the synth module.
    jm_function(
        proj,
        "poly_detune",
        "synth",
        params=[("cents", "float")],
        return_type="float",
        doc="Convert cents to frequency ratio.",
    )

    # Sanity-check the scaffold before removing anything.
    cfg = C.load(proj)
    assert "osc" in C.components(cfg), "osc should exist after scaffold"
    assert "env" in C.components(cfg), "env should exist after scaffold"
    method_names = [m["name"] for m in cfg.get("osc", {}).get("methods", [])]
    assert "tune" in method_names, "tune method should exist before removal"
    prop_names = [p["name"] for p in cfg.get("env", {}).get("properties", [])]
    assert "clipping" in prop_names, (
        "clipping property should exist before removal"
    )
    fn_names = [
        f["name"]
        for f in cfg.get("module", {}).get("synth", {}).get("functions", [])
    ]
    assert "poly_detune" in fn_names, "poly_detune should exist before removal"

    # ── 2. Remove method tune from osc ────────────────────────────────────────

    jm_remove(proj, "method", "tune", object_name="osc", force=True)

    cfg = C.load(proj)
    method_names = [m["name"] for m in cfg.get("osc", {}).get("methods", [])]
    # The TOML entry must be gone — osc no longer owns a tune method.
    assert "tune" not in method_names, (
        "tune must be absent from TOML after removal"
    )

    # For module objects the PyMethodDef entries live in the per-object fragment
    # (synth_ext_osc.c), not in the aggregated synth_ext.c. Osc_tune is the
    # binding name generated for the tune method; its absence from the fragment
    # confirms the method table was rebuilt from the updated config.
    frag_osc = (
        proj / "native" / "src" / "synth" / "synth_ext_osc.c"
    ).read_text()
    assert "Osc_tune" not in frag_osc, (
        "Python binding Osc_tune must not appear in regenerated osc fragment"
    )
    # The aggregated synth_ext.c uses OscType (not OscObject) and must still
    # include the osc fragment — the object type registration must survive.
    ext = (proj / "native" / "src" / "synth" / "synth_ext.c").read_text()
    assert "OscType" in ext, (
        "OscType must still be registered in aggregated ext.c"
    )
    assert '"synth_ext_osc.c"' in ext, (
        "osc fragment include must remain in synth_ext.c after method removal"
    )

    # ── 3. Remove property clipping from env ──────────────────────────────────

    jm_remove(proj, "property", "clipping", object_name="env", force=True)

    cfg = C.load(proj)
    prop_names = [p["name"] for p in cfg.get("env", {}).get("properties", [])]
    # The TOML property list must no longer contain clipping.
    assert "clipping" not in prop_names, (
        "clipping must be absent from TOML after removal"
    )
    # For module objects the PyGetSetDef entries live in the per-object fragment
    # (synth_ext_env.c). A read-only property produces an entry named
    # "clipping" in that table; its absence confirms the getset table was
    # rebuilt from the updated property list.
    frag_env = (
        proj / "native" / "src" / "synth" / "synth_ext_env.c"
    ).read_text()
    assert '"clipping"' not in frag_env, (
        '"clipping" PyGetSetDef entry must be gone from regenerated env fragment'
    )
    # The aggregated synth_ext.c must still register EnvType — the object
    # itself was not removed, only one property on it.
    ext = (proj / "native" / "src" / "synth" / "synth_ext.c").read_text()
    assert "EnvType" in ext, (
        "EnvType must still be registered in aggregated ext.c"
    )

    # ── 4. Remove module-level function poly_detune from synth ────────────────

    jm_remove(proj, "function", "poly_detune", module="synth", force=True)

    cfg = C.load(proj)
    fn_names = [
        f["name"]
        for f in cfg.get("module", {}).get("synth", {}).get("functions", [])
    ]
    # The TOML functions list must be empty (or absent) after the only function
    # is removed — no stale entry should remain.
    assert "poly_detune" not in fn_names, (
        "poly_detune must be absent from TOML after removal"
    )
    # _bind_poly_detune is the C wrapper generated into ext.c; its absence
    # confirms the module was regenerated from the updated function list.
    ext = (proj / "native" / "src" / "synth" / "synth_ext.c").read_text()
    assert "_bind_poly_detune" not in ext, (
        "_bind_poly_detune wrapper must be gone from ext.c"
    )

    # ── 5. Remove state field decay from env ────────────────────────────────

    jm_remove(proj, "state", "decay", object_name="env", force=True)

    cfg = C.load(proj)
    state_names = [s["name"] for s in cfg.get("env", {}).get("state", [])]
    # decay is gone; the remaining fields must be intact.
    assert "decay" not in state_names, (
        "decay must be absent from TOML after removal"
    )
    # attack and level were not removed — confirm the TOML preserved them.
    assert "attack" in state_names, "attack must survive decay removal"
    assert "level" in state_names, "level must survive decay removal"

    # State removal triggers regeneration of env_core.h so the struct definition
    # and the constructor signature reflect only the surviving fields.
    core_h = (proj / "native" / "inc" / "env" / "env_core.h").read_text()
    # The struct field and any constructor default for "decay" are gone.
    assert "decay" not in core_h, (
        "'decay' must not appear in regenerated env_core.h"
    )
    # The surviving fields must still be present in the struct.
    assert "attack" in core_h, "attack field must remain in env_core.h"

    # ── 6. Remove entire osc object ───────────────────────────────────────────

    jm_remove(proj, "object", "osc", force=True)

    cfg = C.load(proj)
    # osc must be completely absent from the component registry.
    assert "osc" not in C.components(cfg), (
        "osc must be absent from components() after object removal"
    )
    # Generated directory trees for osc must be deleted.
    assert not (proj / "native" / "inc" / "osc").exists(), (
        "native/inc/osc/ must be deleted after object removal"
    )
    assert not (proj / "native" / "src" / "osc").exists(), (
        "native/src/osc/ must be deleted after object removal"
    )
    # env is a sibling in the same module and must be completely unaffected.
    assert "env" in C.components(cfg), (
        "env must still be present after osc is removed"
    )
    assert (proj / "native" / "inc" / "env").exists(), (
        "native/inc/env/ must survive osc removal"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("jm_remove: PASSED")
