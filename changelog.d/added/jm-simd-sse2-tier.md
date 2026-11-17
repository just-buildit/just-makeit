- **`jm_simd.h` has an SSE2 tier: four float lanes on every x86-64
    build.** A portable build (no `-march`, or `x86-64-v2`) fell to the
    one-lane scalar tier; it now runs `__m128`, and AVX2 + FMA still wins
    when the flags allow. SSE has no fused multiply-add, so a MAC there is
    a multiply then an add and can differ from the scalar tier in the last
    place. A `JM_DEFINE_STEPS` object with `LENGTH = 0` no longer needs a
    `state->delay` placeholder on any build: the history copy the SIMD path
    did around each batch is not emitted when `LENGTH` is the token `0`,
    so a stateless object that compiled on a portable x86-64 build still
    does now that a SIMD tier is active there, and one on aarch64 can drop
    its placeholder.
