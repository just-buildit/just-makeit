#include "/*<<component>>*///*<<component>>*/_core.h"

/*<<component>>*/_state_t *
/*<<component>>*/_create(/*<<create_params>>*/)
{
    /*<<component>>*/_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
/*<<create_assignments>>*/
    return obj;
}

/*<<destroy_c_ret>>*/
/*<<component>>*/_destroy(/*<<component>>*/_state_t *state)
{
/*<<destroy_impl>>*/    free(state);/*<<destroy_ret_stmt>>*/
}

void
/*<<component>>*/_reset(/*<<component>>*/_state_t *state)
{
/*<<reset_assignments>>*/
}

/*<<steps_c_impl>>*/

/*<<getter_setter_impls>>*/
