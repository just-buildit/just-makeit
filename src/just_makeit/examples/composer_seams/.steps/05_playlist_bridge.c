/* The bodies behind playlist_bridge.h.
 *
 * Note what is NOT here: no Python.h, no PyObject, no reference counting.
 * jm owns the binding that calls these; the algorithms are the project's.
 * The include is the generated header, so these definitions are checked
 * against jm's declarations by the compiler rather than by eye.
 */
#include "studio/playlist/playlist_bridge.h"

/* Seam 1 — source config to running generator. A real one would derive
 * increments from `fs`; this one just carries the level across, and refuses
 * a configuration it cannot honour. */
clip_state_t *
clip_from_source (const clip_t *src, double fs)
{
  (void)fs;
  if (src->gain < 0.0)
    return NULL;
  return clip_create (src->gain);
}

/* ...and why. Called only after clip_from_source returned NULL, with the
 * same arguments: a sentence here is raised as ValueError; NULL means "no
 * reason to give" and keeps the RuntimeError. */
const char *
clip_why_not (const clip_t *src, double fs)
{
  (void)fs;
  if (src->gain < 0.0)
    return "a clip's gain must be >= 0";
  return NULL;
}

/* Seam 2 — a quantity derived from the config, never stored beside it. */
double
clip_duration (const clip_t *src)
{
  return src->gain * 2.0;
}
