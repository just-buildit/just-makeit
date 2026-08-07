/* bench_/*<<component>>*/_core.c — no step() to benchmark */
#include "/*<<component>>*///*<<component>>*/_core.h"
#include "jm_bench.h"
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
/*<<bench_create_stmt>>*/
    struct timespec t0, t1;
    jm_bench_t _bench = {0};

    printf("=== /*<<component>>*/ benchmark ===\n");
    /* gh-806: "methods below" was a promise, not a fact -- a component whose
     * methods are all variable_output / out_type / varargs / codec has no
     * benchable shape among them, and this line read as though it did.  What
     * actually got measured is reported by jm_bench_write_json(). */
    printf("  (no step())\n");
    printf("block = %d samples,  %d iterations\n\n", BENCH_N, ITERATIONS);

/*<<bench_methods_timing_block>>*/
    jm_bench_write_json(&_bench, "/*<<component>>*/");
/*<<bench_destroy_stmt>>*/
    return 0;
}
