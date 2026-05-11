/**
 * @file engine_core.h
 * @brief Engine component API.
 *
 * Lifecycle: create → [step / steps / reset]* → destroy
 *
 * Example:
 * @code
 * engine_state_t *obj = engine_create(1.0);
 * float complex y = engine_step(obj, 0.0f + 0.0f * I);
 * engine_destroy(obj);
 * @endcode
 */
#ifndef ENGINE_CORE_H
#define ENGINE_CORE_H

#include "clib_common.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Engine state.
 *
 * Opaque to callers — allocate with engine_create().
 */
typedef struct {
    double gain;
} engine_state_t;

/**
 * @brief Create a engine instance.
 *
 * @param gain  Initial gain (default: 1.0).
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call engine_destroy() when done.
 */
engine_state_t *engine_create(double gain);

/**
 * @brief Destroy a engine instance and release all memory.
 * @param state  May be NULL.
 */
void engine_destroy(engine_state_t *state);

/**
 * @brief Reset engine to its post-create state.
 * @param state  Must be non-NULL.
 */
void engine_reset(engine_state_t *state);

/**
 * @brief Process a single complex sample.
 *
 * @param state  Component state.
 * @param x      Input sample.
 * @return       Output sample.
 * @note Inlined for maximum performance.
 */
static inline float complex
engine_step(const engine_state_t *state, float complex x)
{
    (void)state; /* TODO: implement using state variables */
    return (float complex)x;
}

/**
 * @brief Process a block of samples.
 *
 * @param state   Component state.
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input for in-place).
 * @param n       Number of samples.
 * @note Output buffer must be pre-allocated by caller.
 */
void engine_steps(
    engine_state_t *state,
    const float complex   *input,
    float complex      *output,
    size_t                 n);

/**
 * @brief Get current gain.
 * @param state  Must be non-NULL.
 */
double engine_get_gain(const engine_state_t *state);

/**
 * @brief Set gain.
 * @param state  Must be non-NULL.
 * @param gain  New value.
 */
void engine_set_gain(engine_state_t *state, double gain);

#ifdef __cplusplus
}
#endif

#endif /* ENGINE_CORE_H */
