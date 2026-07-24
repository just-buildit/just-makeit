#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct
{
  size_t used;
} ringbuf_stats_t;
/**
 * @brief Open a fixed-capacity FIFO ring buffer of 32-bit floats.
 *
 * The buffer holds up to `capacity` samples; a push past capacity drops
 * the overflow and reports how many were accepted.
 *
 * @param capacity  Maximum number of samples buffered at once.
 */
ringbuf_t *ringbuf_open (size_t capacity);
void       ringbuf_close (ringbuf_t *r);
/**
 * @brief Append samples to the buffer, scaling each by the current gain.
 * @param x  Samples to append (oldest-to-newest).
 * @return The number of samples accepted; fewer than requested once full.
 * @code
 * >>> import numpy as np
 * >>> from composites.ring import Ring
 * >>> r = Ring(capacity=4)
 * >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
 * 4
 * @endcode
 */
size_t ringbuf_push (ringbuf_t *r, const float *x, size_t n);
/**
 * @brief Remove and return the oldest buffered samples, FIFO order.
 * @param n  Maximum number of samples to remove.
 * @return A new array of the popped samples (up to `n`, fewer if drained).
 */
size_t ringbuf_pop (ringbuf_t *r, float *out, size_t n);
void   ringbuf_stats (const ringbuf_t *r, ringbuf_stats_t *out);
/**
 * @brief The gain applied to every pushed sample.
 */
float ringbuf_get_gain (const ringbuf_t *r);
void  ringbuf_set_gain (ringbuf_t *r, float gain);
#endif
