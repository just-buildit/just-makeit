"""End-to-end test: bench regeneration via `just-makeit upgrade`.

Simulates an existing project (schema 2) that already has a named method
recorded in just-makeit.toml but whose bench file pre-dates method timing
blocks.  After `just-makeit upgrade` the bench file must contain a
self-contained timing block for every benchmarkable method.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/bench_upgrade/test.py
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def run(root: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._method import run as jm_method
    from just_makeit._new import run as jm_new
    from just_makeit._upgrade import run as jm_upgrade

    proj = root / "my_filter"

    # 1. Scaffold a minimal standalone project (lands at CURRENT_SCHEMA).
    jm_new(
        "my_filter",
        proj,
        object_names=["fir"],
        state_vars=[("gain", "double", "1.0")],
    )

    # 2. Add two methods: one scalar, one excluded from bench.
    jm_method(
        root=proj,
        object_name="fir",
        method_name="configure",
        module=None,
        arg_type="double",
        return_type="void",
        variable_output=False,
        multi_output=[],
    )
    jm_method(
        root=proj,
        object_name="fir",
        method_name="internal_reset",
        module=None,
        arg_type="void",
        return_type="void",
        variable_output=False,
        multi_output=[],
        no_bench=True,
    )

    bench_c = proj / "native" / "benchmarks" / "bench_fir_core.c"

    # 3. Verify the freshly-generated bench already has configure() block.
    fresh = bench_c.read_text(encoding="utf-8")
    assert "bench: configure()" in fresh, "fresh bench missing configure block"
    assert "bench: internal_reset()" not in fresh, (
        "internal_reset should be excluded via --no-bench"
    )

    # 4. Simulate a pre-feature project: rewrite bench without method blocks
    #    and set schema back to 2 so upgrade has work to do.
    old_bench = re.sub(
        r"\n\s*/\* bench:.*",  # strip any method block remnants (none yet, but safe)
        "",
        "#include \"fir/fir_core.h\"\n/* old bench — no method blocks */\nint main(void) { return 0; }\n",
    )
    bench_c.write_text(old_bench, encoding="utf-8")

    cfg = C.load(proj)
    C.set_schema_version(cfg, 2)
    C.save(proj, cfg)

    # Confirm downgrade took effect.
    assert C.schema_version(C.load(proj)) == 2

    # 5. Run upgrade — should advance schema and regenerate bench.
    jm_upgrade(proj)

    # 6. Verify schema advanced to current.
    assert C.schema_version(C.load(proj)) == C.CURRENT_SCHEMA

    # 7. Verify bench file now has configure() timing block.
    upgraded = bench_c.read_text(encoding="utf-8")
    assert "bench: configure()" in upgraded, (
        "upgrade did not add configure() timing block"
    )
    assert "bench: internal_reset()" not in upgraded, (
        "internal_reset must remain excluded after upgrade"
    )
    assert "<<" not in upgraded, "unresolved placeholder in upgraded bench"

    # 8. Verify the timing block structure for the scalar void-arg method.
    assert "fir_configure(obj, 0.0)" in upgraded, (
        "configure() bench should call with zero arg"
    )
    assert "for (int i = 0; i < BENCH_N; i++)" in upgraded, (
        "scalar method should use inner loop"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("bench_upgrade: PASSED")
