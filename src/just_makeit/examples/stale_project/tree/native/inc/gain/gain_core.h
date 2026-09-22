/**
 * @file gain_core.h
 * @brief Gain component API.
 *
 * Lifecycle: create -> [step / steps / reset]* -> destroy
 *
 * Example:
 * @code
 * gain_state_t *obj = gain_create(1.0);
 * float y = gain_step(obj, 0.0f);
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
 * Allocate with gain_create().
 */
typedef struct {
    float level;

} gain_state_t;

/**
 * @brief Create a gain instance.
 *
 * @param level  Initial level (default: 1.0).
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call gain_destroy() when done.
 */
gain_state_t *gain_create(float level);

/**
 * @brief Destroy a gain instance and release all memory.
 * @param state  May be NULL.
 */
void gain_destroy(gain_state_t *state);

/**
 * @brief Reset Gain to its post-create state.
 * @param state  Must be non-NULL.
 */
void gain_reset(gain_state_t *state);

/**
 * @brief Process one input sample.
 * @param state  Must be non-NULL.
 * @param x      Input sample (float).
 * @return Output sample (float).
 */
static inline float
gain_step(const gain_state_t *state, float x)
{
    (void)state; /* TODO: implement using state variables */
    return (float)x;
}

/**
 * @brief Process a block of samples.
 *
 * @param state   Component state (mutated).
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input for in-place).
 * @param n       Number of samples.
 */
void gain_steps(
    gain_state_t *state,
    const float    *input,
    float          *output,
    size_t               n);

/**
 * @brief Get current level.
 * @param state  Must be non-NULL.
 */
float gain_get_level(const gain_state_t *state);

/**
 * @brief Set level.
 * @param state  Must be non-NULL.
 * @param val    New value.
 */
void gain_set_level(gain_state_t *state, float val);



#ifdef __cplusplus
}
#endif

#endif /* GAIN_CORE_H */
