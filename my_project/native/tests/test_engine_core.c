#include "engine/engine_core.h"
#include <complex.h>
#include <stdio.h>

#define CHECK(cond) \
    do { if (!(cond)) { \
        fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
        _fails++; \
    } } while (0)

int main(void)
{
    int _fails = 0;
    engine_state_t *obj = engine_create(1.0);
    CHECK(obj != NULL);
    if (!obj) return 1;

    /* step: verify it runs */
    (void)engine_step(obj, 0.0f + 0.0f * I);

    /* gain: getter / setter */
    CHECK(engine_get_gain(obj) == 1.0);
    engine_set_gain(obj, 2.0);
    CHECK(engine_get_gain(obj) == 2.0);

    /* reset restores defaults */
    engine_set_gain(obj, 2.0);
    engine_reset(obj);
    CHECK(engine_get_gain(obj) == 1.0);

    engine_destroy(obj);
    if (_fails) {
        fprintf(stderr, "test_engine_core FAILED (%d)\n", _fails);
        return 1;
    }
    printf("test_engine_core PASSED\n");
    return 0;
}
