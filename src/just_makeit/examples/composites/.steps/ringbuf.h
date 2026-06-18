#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct { size_t used; } ringbuf_stats_t;
ringbuf_t *ringbuf_open(size_t capacity);
void ringbuf_close(ringbuf_t *r);
size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n);
size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n);
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out);
float ringbuf_get_gain(const ringbuf_t *r);
void ringbuf_set_gain(ringbuf_t *r, float gain);
#endif
