#include "/*<<inc_prefix>>*//*<<component>>*///*<<component>>*/_core.h"
#include "jm_bench.h"
#include "/*<<inc_prefix>>*/clib_common.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define BENCH_N    65536
#define ITERATIONS 200

int
main(void)
{
/*<<bench_in_decl>>*/
/*<<bench_out_decl>>*/
/*<<bench_in_loop>>*/

    /*<<component>>*/_state_t *obj = /*<<create_name>>*/(/*<<c_create_args>>*/);

/*<<bench_volatile_sink>>*/

    /* warmup */
    for (int i = 0; i < 16; i++) /*<<bench_sink_assign>>*//*<<bench_warmup_fn>>*/(obj/*<<bench_step_input_sep>>*//*<<bench_step_input_arg>>*/);

    uint64_t t0, t1;
    jm_bench_t _bench = {0};

    printf("=== /*<<component>>*/ benchmark ===\n");
    printf("block = %d samples,  %d iterations\n\n", BENCH_N, ITERATIONS);

/*<<bench_step_timing_block>>*/
/*<<bench_steps_timing_block>>*/
/*<<bench_methods_timing_block>>*/
    jm_bench_write_json(&_bench, "/*<<component>>*/");
    /*<<component>>*/_destroy(obj);
/*<<bench_free_in>>*/
/*<<bench_free_out>>*/
    return 0;
}
