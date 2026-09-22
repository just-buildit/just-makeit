#include "fir/fir_core.h"
#include <complex.h>
#include <math.h>
#include <stdio.h>

#define CHECK(cond) \
    do { if (!(cond)) { \
        fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
        _fails++; \
    } } while (0)

/* Floating-point helpers — use inline functions, not macros, so arguments
 * are evaluated exactly once.  Safe to call with stateful step() results. */
static inline int _almost_eq(float a, float b, float tol)
    { return fabsf(a - b) <= tol; }
static inline int _almost_eq_c(float complex a, float complex b, float tol)
    { return _almost_eq(crealf(a), crealf(b), tol)
          && _almost_eq(cimagf(a), cimagf(b), tol); }
#define ALMOST_EQ(a, b, tol)   _almost_eq((float)(a),         (float)(b),         tol)
#define ALMOST_EQ_C(a, b, tol) _almost_eq_c((float complex)(a), (float complex)(b), tol)

int main(void)
{
    int _fails = 0;
    fir_state_t *obj = fir_create(1.0);
    CHECK(obj != NULL);
    if (!obj) return 1;

    /* scale: getter / setter */
    CHECK(fir_get_scale(obj) == 1.0);
    fir_set_scale(obj, 2.0f);
    CHECK(fir_get_scale(obj) == 2.0f);

    /* step: verify it runs without crashing */
    (void)fir_step(obj, 0.0f);

    /* reset restores defaults */
    fir_set_scale(obj, 2.0f);
    fir_reset(obj);
    CHECK(fir_get_scale(obj) == 1.0);

    fir_destroy(obj);
    if (_fails) {
        fprintf(stderr, "test_fir_core FAILED (%d)\n", _fails);
        return 1;
    }
    printf("test_fir_core PASSED\n");
    return 0;
}
