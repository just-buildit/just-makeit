/**
 * @file gain_core.h
 * @brief Gain component API.
 *
 * Lifecycle: create → [step / steps / reset]* → destroy
 *
 * Example:
 * @code
 * gain_state_t *obj = gain_create(1.0);
 * float complex y = gain_step(obj, 1.0f + 0.0f * I);
 * gain_destroy(obj);
 * @endcode
 */
#ifndef GAIN_CORE_H
#define GAIN_CORE_H

#include "clib_common.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Gain state.
 *
 * Opaque to callers — allocate with gain_create().
 */
typedef struct {
    double gain;
} gain_state_t;

/**
 * @brief Create a gain instance.
 *
 * @param gain  Initial gain value.
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call gain_destroy() when done.
 */
gain_state_t *gain_create(double gain);

/**
 * @brief Destroy a gain instance and release all memory.
 * @param state  May be NULL.
 */
void gain_destroy(gain_state_t *state);

/**
 * @brief Reset gain to its post-create state.
 * @param state  Must be non-NULL.
 */
void gain_reset(gain_state_t *state);

/**
 * @brief Process a single complex sample.
 *
 * @param state  Component state.
 * @param x      Input sample.
 * @return       Output sample.
 * @note Inlined for maximum performance.
 */
static inline float complex gain_step(const gain_state_t *state, float complex x) {
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}

/**
 * @brief Process a block of complex samples.
 *
 * @param state   Component state.
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input for in-place).
 * @param n       Number of samples.
 * @note Output buffer must be pre-allocated by caller.
 */
void gain_steps(gain_state_t *state, const float complex *input, float complex *output, size_t n);

/**
 * @brief Get current gain.
 * @param state  Must be non-NULL.
 */
double gain_get_gain(const gain_state_t *state);

/**
 * @brief Set gain.
 * @param state  Must be non-NULL.
 * @param gain  New value.
 */
void gain_set_gain(gain_state_t *state, double gain);

#ifdef __cplusplus
}
#endif

#endif /* GAIN_CORE_H */
