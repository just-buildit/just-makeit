"""Drive the four generated OO types, and both seams."""

import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402

from studio.playlist.playlist import Clip, Mix, Track  # noqa: E402

# ── seam 2: a computed property ──────────────────────────────────────────
clip = Clip(gain=2.0)
print(f"Clip(gain=2.0).gain      -> {clip.gain}")
print(f"           .duration     -> {clip.duration}   (clip_duration, in C)")
assert clip.duration == 4.0

# Derived on every read, so reassigning the field it depends on is enough.
# A stored attribute would still be reporting 4.0 here.
clip.gain = 5.0
print(f"  after gain = 5.0       -> {clip.duration}  (recomputed, not stored)")
assert clip.duration == 10.0

# It is read-only: there is no field behind it to assign to.
try:
    clip.duration = 1.0
    raise AssertionError("expected a read-only property")
except AttributeError:
    print("           .duration = 1.0 -> AttributeError (read-only)")

# ── seam 1: standalone generation through the bridge ─────────────────────
# Clip.steps() has no kernel of its own; it calls clip_from_source() to build
# a clip_state_t and then drives the generator jm never had to know about.
block = Clip(gain=7.0, fs=1.0).steps(3)
print(f"Clip(gain=7.0).steps(3)  -> {block}   (via clip_from_source)")
assert isinstance(block, np.ndarray)
assert np.allclose(block, [7 + 0j, 7 + 0j, 7 + 0j])

# ── the composed object-of-objects ───────────────────────────────────────
# Track sums its sources; Mix sequences tracks and runs the backing kernel.
track = Track.sum(Clip(gain=2.0), Clip(gain=3.0), dur=4)
mix = Mix(track)
out = mix.execute(8)
print(f"Mix(Track.sum(2,3,dur=4)).execute(8) -> {out}")
# Four samples of 2+3, then the finite spec runs out -- execute() returns a
# short block rather than padding.
assert np.allclose(out, [5 + 0j] * 4)
assert len(out) == 4

# The resolved spec reflects back as rebuilt OO objects -- read from the
# kernel through playlist_segments(), not cached from what was passed in.
print(
    f"mix.segments             -> {len(mix.segments)} track(s), "
    f"repeat={mix.repeat}, continuous={mix.continuous}"
)
assert len(mix.segments) == 1
assert isinstance(mix.segments[0], Track)
assert mix.repeat is False and mix.continuous is False

print("composer_seams demo: PASSED")
