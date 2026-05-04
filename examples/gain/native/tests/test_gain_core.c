#include "gain/gain_core.h"
#include <assert.h>
#include <complex.h>
#include <stdio.h>

int main(void)
{
    gain_state_t *obj = gain_create(1.0);
    assert(obj != NULL);

    /* step: pass-through */
    float complex y = gain_step(obj, 1.0f + 0.0f * I);
    assert(crealf(y) == 1.0f);
    assert(cimagf(y) == 0.0f);

    /* gain: getter / setter */
    assert(gain_get_gain(obj) == 1.0);
    gain_set_gain(obj, 2.0);
    assert(gain_get_gain(obj) == 2.0);

    /* reset zeros all variables */
    gain_reset(obj);
    assert(gain_get_gain(obj) == 0.0);

    gain_destroy(obj);
    printf("test_gain_core PASSED\n");
    return 0;
}
