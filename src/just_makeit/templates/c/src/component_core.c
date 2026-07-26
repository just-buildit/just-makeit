#include "/*<<component>>*///*<<component>>*/_core.h"
/*<<state_struct_def>>*/
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
/*<<reset_c_open>>*//*<<reset_assignments>>*//*<<reset_c_close>>*/
/*<<steps_c_impl>>*/

/*<<getter_setter_impls>>*/
