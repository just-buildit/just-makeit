/* The bodies behind playlist_bridge.h.
 *
 * Note what is NOT here: no Python.h, no PyObject, no reference counting.
 * jm owns the binding that calls these; the algorithms are the project's.
 * The include is the generated header, so these definitions are checked
 * against jm's declarations by the compiler rather than by eye.
 */
#include "playlist/playlist_bridge.h"

/* Seam 1 — source config to running generator. A real one would derive
 * increments from `fs`; this one just carries the level across. */
clip_state_t *
clip_from_source (const clip_t *src, double fs)
{
  (void)fs;
  return clip_create (src->gain);
}

/* Seam 2 — a quantity derived from the config, never stored beside it. */
double
clip_duration (const clip_t *src)
{
  return src->gain * 2.0;
}
