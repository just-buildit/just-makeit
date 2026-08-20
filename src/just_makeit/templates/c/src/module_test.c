/* test_/*<<module>>*/_core.c — smoke test for the /*<<module>>*/ module's
 * free functions.
 *
 * gh-1034: jm generates and owns a function-only module, and used to generate
 * no C test for it — so the one component whose C jm writes end to end was
 * the one with nothing checking it. An object has had this file since the
 * beginning.
 */
#include "/*<<module>>*///*<<module>>*/_core.h"

/* Both defines come BEFORE the include: jm_test.h defaults each if the
 * including file has not set it, so a later define would be ignored. */
#define JM_TEST_NAME       "test_/*<<module>>*/_core"
#define JM_SCAFFOLD_CHECKS /*<<scaffold_checks>>*/

#include "jm_test.h"

int main(void)
{
/*<<module_fn_smoke_calls>>*/
    JM_TEST_EPILOGUE();
}
