/**
 * @file fir_filter_core.h
 * @brief FirFilter component API.
 *
 * Lifecycle: create → [step / steps / reset]* → destroy
 *
 * Example:
 * @code
 * fir_filter_state_t *obj = fir_filter_create(1.0f);
 * float complex y = fir_filter_step(obj, 1.0f + 0.0f * I);
 * fir_filter_destroy(obj);
 * @endcode
 */
#ifndef FIR_FILTER_CORE_H
#define FIR_FILTER_CORE_H

#include "clib_common.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief FirFilter state.
 *
 * Opaque to callers — allocate with fir_filter_create().
 */
typedef struct {
    float coeffs[16];
    float _Complex delay[16];
    float gain;
} fir_filter_state_t;

/**
 * @brief Create a fir_filter instance.
 *
 * @param gain  Initial gain (default: 1.0f).
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call fir_filter_destroy() when done.
 */
fir_filter_state_t *fir_filter_create(float gain);

/**
 * @brief Destroy a fir_filter instance and release all memory.
 * @param state  May be NULL.
 */
void fir_filter_destroy(fir_filter_state_t *state);

/**
 * @brief Reset fir_filter to its post-create state.
 * @param state  Must be non-NULL.
 */
void fir_filter_reset(fir_filter_state_t *state);

/**
 * @brief Process a single complex sample.
 *
 * Shifts the delay line, inserts x at delay[0], then computes
 *   y = gain * sum_k( coeffs[k] * delay[k] )
 *
 * @param state  Component state (mutated — delay line is updated).
 * @param x      Input sample.
 * @return       Filtered output sample.
 * @note Inlined for maximum performance.
 */
static inline float complex
fir_filter_step(fir_filter_state_t *state, float complex x)
{
    memmove(&state->delay[1], &state->delay[0], (16 - 1) * sizeof(float complex));
    state->delay[0] = x;

    float complex y = 0.0f + 0.0f * I;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];

    return (float complex)state->gain * y;
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
void fir_filter_steps(
    fir_filter_state_t *state,
    const float complex    *input,
    float complex          *output,
    size_t                  n);

/**
 * @brief Get current gain.
 * @param state  Must be non-NULL.
 */
float fir_filter_get_gain(const fir_filter_state_t *state);

/**
 * @brief Set gain.
 * @param state  Must be non-NULL.
 * @param gain  New value.
 */
void fir_filter_set_gain(fir_filter_state_t *state, float gain);

/**
 * @brief Copy coeffs into dest.
 * @param state  Must be non-NULL.
 * @param dest   Output buffer of length 16.
 */
void fir_filter_get_coeffs(const fir_filter_state_t *state, float *dest);

/**
 * @brief Get a read-only pointer to coeffs.
 * @param state  Must be non-NULL.
 * @return Pointer valid until fir_filter_destroy() is called.
 */
const float *fir_filter_get_coeffs_view(const fir_filter_state_t *state);

/**
 * @brief Set coeffs from src.
 * @param state  Must be non-NULL.
 * @param src    Source buffer of length 16.
 */
void fir_filter_set_coeffs(fir_filter_state_t *state, const float *src);

/**
 * @brief Copy delay into dest.
 * @param state  Must be non-NULL.
 * @param dest   Output buffer of length 16.
 */
void fir_filter_get_delay(const fir_filter_state_t *state, float _Complex *dest);

/**
 * @brief Get a read-only pointer to delay.
 * @param state  Must be non-NULL.
 * @return Pointer valid until fir_filter_destroy() is called.
 */
const float _Complex *fir_filter_get_delay_view(const fir_filter_state_t *state);

/**
 * @brief Set delay from src.
 * @param state  Must be non-NULL.
 * @param src    Source buffer of length 16.
 */
void fir_filter_set_delay(fir_filter_state_t *state, const float _Complex *src);

#ifdef __cplusplus
}
#endif

#endif /* FIR_FILTER_CORE_H */
