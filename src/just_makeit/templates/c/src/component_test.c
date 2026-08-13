#include "/*<<component>>*///*<<component>>*/_core.h"

/* gh-806: how many assertions just-makeit generated into this file.  Stamped
 * at scaffold time and compared against the runtime count by
 * JM_TEST_EPILOGUE(), so the "no assertions beyond the scaffold" note clears
 * itself the moment an author adds a check of their own -- a hard-coded
 * marker would still be claiming to be a placeholder long after the real
 * suite was written.
 *
 * Both defines come BEFORE the include: jm_test.h defaults each if the
 * including file has not set it, so a later define would be ignored. */
#define JM_TEST_NAME       "test_/*<<component>>*/_core"
#define JM_SCAFFOLD_CHECKS /*<<scaffold_checks>>*/

/* gh-934: CHECK, REQUIRE, the counters, the float helpers and the epilogue
 * all live in jm_test.h, written once per project.  They used to be stamped
 * into this template, so every component carried another private copy that
 * diverged from the day it was written. */
#include "jm_test.h"

int main(void)
{
    /*<<component>>*/_state_t *obj = /*<<component>>*/_create(/*<<c_create_args>>*/);
/*<<obj_null_check>>*/

/*<<getter_setter_test_c>>*/

/*<<step_c_smoke_test>>*/

/*<<reset_test_c>>*/

    /*<<component>>*/_destroy(obj);
    JM_TEST_EPILOGUE();
}
