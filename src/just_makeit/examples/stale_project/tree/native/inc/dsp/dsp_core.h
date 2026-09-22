/**
 * @file dsp_core.h
 * @brief Dsp module — public C API.
 */
#ifndef DSP_CORE_H
#define DSP_CORE_H

#include "clib_common.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Declare module-level functions here. */

double energy(int n, float *y, size_t y_len);


#ifdef __cplusplus
}
#endif

#endif /* DSP_CORE_H */
