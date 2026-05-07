#include "fir_filter/fir_filter_core.h"
#include <assert.h>
#include <complex.h>
#include <math.h>
#include <stdio.h>

/* Allow ±1 ULP of float32 error. */
static int approxf(float a, float b) { return fabsf(a - b) < 1e-5f; }

int main(void)
{
    fir_filter_state_t *obj = fir_filter_create(1.0f);
    assert(obj != NULL);

    /* step: identity filter (coeffs[0]=1, rest zero → delay-free passthrough) */
    {
        float h[16] = {0};
        h[0] = 1.0f;
        fir_filter_set_coeffs(obj, h);

        float complex y0 = fir_filter_step(obj, 3.0f + 4.0f * I);
        assert(approxf(crealf(y0), 3.0f));
        assert(approxf(cimagf(y0), 4.0f));

        float complex y1 = fir_filter_step(obj, 0.0f + 0.0f * I);
        assert(approxf(crealf(y1), 0.0f));
        assert(approxf(cimagf(y1), 0.0f));
    }

    fir_filter_reset(obj);

    /* steps: impulse response matches the loaded coefficients */
    {
        float h[16] = {0};
        h[0] = 0.25f; h[1] = 0.5f; h[2] = 0.25f;
        fir_filter_set_coeffs(obj, h);

        float complex in[16]  = {0};
        float complex out[16] = {0};
        in[0] = 1.0f + 0.0f * I;  /* unit impulse */

        fir_filter_steps(obj, in, out, 16);

        assert(approxf(crealf(out[0]), 0.25f));
        assert(approxf(crealf(out[1]), 0.5f));
        assert(approxf(crealf(out[2]), 0.25f));
        assert(approxf(crealf(out[3]), 0.0f));

        assert(approxf(cimagf(out[0]), 0.0f));
        assert(approxf(cimagf(out[1]), 0.0f));
    }

    fir_filter_reset(obj);

    /* gain: getter / setter */
    assert(fir_filter_get_gain(obj) == 1.0f);
    fir_filter_set_gain(obj, 2.0f);
    assert(fir_filter_get_gain(obj) == 2.0f);

    /* coeffs: getter / setter */
    {
        float src[16], dst[16];
        src[0] = 2.0f;
        fir_filter_set_coeffs(obj, src);
        fir_filter_get_coeffs(obj, dst);
        assert(dst[0] == 2.0f);
    }

    /* delay: getter / setter */
    {
        float _Complex src[16], dst[16];
        src[0] = 2.0f + 0.0f * I;
        fir_filter_set_delay(obj, src);
        fir_filter_get_delay(obj, dst);
        assert(dst[0] == 2.0f + 0.0f * I);
    }

    /* reset restores defaults */
    fir_filter_set_gain(obj, 2.0f);
    {
        float ones[16];
        size_t i_; for (i_ = 0; i_ < 16; i_++) ones[i_] = 2.0f;
        fir_filter_set_coeffs(obj, ones);
    }
    {
        float _Complex ones[16];
        size_t i_; for (i_ = 0; i_ < 16; i_++) ones[i_] = 2.0f + 0.0f * I;
        fir_filter_set_delay(obj, ones);
    }
    fir_filter_reset(obj);
    assert(fir_filter_get_gain(obj) == 1.0f);
    {
        float buf[16];
        fir_filter_get_coeffs(obj, buf);
        assert(buf[0] == 0.0f);
    }
    {
        float _Complex buf[16];
        fir_filter_get_delay(obj, buf);
        assert(buf[0] == 0.0f + 0.0f * I);
    }

    fir_filter_destroy(obj);
    printf("test_fir_filter_core PASSED\n");
    return 0;
}
