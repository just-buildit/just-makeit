// demo.c
#include "running_stats/running_stats_core.h"
#include <complex.h>
#include <stdio.h>

int main(void) {
    running_stats_state_t *s = running_stats_create(0, 0.0, 0.0);

    double        data[] = {2, 4, 4, 4, 5, 5, 7, 9};
    float complex y;
    for (int i = 0; i < 8; i++)
        y = running_stats_step(s, (float)data[i] + 0.0f * I);

    printf("n:        %d\n", running_stats_get_n(s));      /* 8     */
    printf("mean:     %.4f\n", running_stats_get_mean(s)); /* 5.0000 */
    printf("variance: %.4f\n", (double)cimagf(y));         /* 4.0000 */

    running_stats_reset(s);
    printf("after reset: n=%d mean=%.1f\n", running_stats_get_n(s),
           running_stats_get_mean(s)); /* n=0 mean=0.0 */

    running_stats_destroy(s);
    return 0;
}
