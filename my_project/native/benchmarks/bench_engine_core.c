#include "engine/engine_core.h"
#include <complex.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define BENCH_N    65536
#define ITERATIONS 200

static double
elapsed_sec(struct timespec *t0, struct timespec *t1)
{
    return (double)(t1->tv_sec - t0->tv_sec)
           + (double)(t1->tv_nsec - t0->tv_nsec) * 1e-9;
}

int
main(void)
{
    float complex *in  = malloc(BENCH_N * sizeof(float complex));
    float complex *out = malloc(BENCH_N * sizeof(float complex));
    if (!in || !out) { fprintf(stderr, "OOM\n"); return 1; }
    for (int i = 0; i < BENCH_N; i++) in[i] = (float)(i)+ 0.0f * I;

    engine_state_t *obj = engine_create(1.0);

    /* warmup */
    for (int i = 0; i < 16; i++) (void)engine_step(obj, 1.0f + 0.0f * I);

    struct timespec t0, t1;
    double sec;

    printf("=== engine benchmark ===\n");
    printf("block = %d samples,  %d iterations\n\n", BENCH_N, ITERATIONS);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        for (int i = 0; i < BENCH_N; i++)
            (void)engine_step(obj, in[i]);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  step()   %8.1f MSa/s\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        engine_steps(obj, in, out, BENCH_N);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  steps()  %8.1f MSa/s\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    engine_destroy(obj);
    free(in); free(out);
    return 0;
}
