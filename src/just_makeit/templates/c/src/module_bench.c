/* bench_/*<<module>>*/_core.c — benchmarks for the /*<<module>>*/ module's
 * free functions (gh-1034).
 */
#include "/*<<module>>*///*<<module>>*/_core.h"
#include "jm_bench.h"
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define BENCH_N    65536
#define ITERATIONS 200

int
main(void)
{
    jm_bench_t _bench = {0};

    printf("=== /*<<module>>*/ benchmark ===\n");
    printf("block = %d samples,  %d iterations\n\n", BENCH_N, ITERATIONS);

/*<<bench_todo>>*/
    jm_bench_write_json(&_bench, "/*<<module>>*/");
    return 0;
}
