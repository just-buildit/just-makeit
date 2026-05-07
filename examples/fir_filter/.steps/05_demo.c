// demo.c
#include "fir_filter/fir_filter_core.h"
#include <complex.h>
#include <stdio.h>

int main(void) {
    fir_filter_state_t *f = fir_filter_create(1.0f);

    float h[16] = {0};
    h[0]        = 0.25f;
    h[1]        = 0.5f;
    h[2]        = 0.25f;
    fir_filter_set_coeffs(f, h);

    /* Read taps without copying — pointer valid until fir_filter_destroy(f) */
    const float *view = fir_filter_get_coeffs_view(f);
    printf("h[1] = %.2f\n", view[1]); /* 0.50 */

    /* Feed a unit impulse */
    float complex in[16]  = {0};
    float complex out[16] = {0};
    in[0]                 = 1.0f + 0.0f * I;
    fir_filter_steps(f, in, out, 16);

    printf("out[0]=%.2f  out[1]=%.2f  out[2]=%.2f\n", crealf(out[0]), crealf(out[1]),
           crealf(out[2])); /* 0.25  0.50  0.25 */

    /* Snapshot the delay line — independent copy */
    float _Complex dl[16];
    fir_filter_get_delay(f, dl);
    printf("delay[0] = %.3f + %.3fj\n", crealf(dl[0]), cimagf(dl[0]));

    fir_filter_reset(f); /* clears delay and coeffs, restores gain = 1.0f */
    fir_filter_destroy(f);
    return 0;
}
