#include "/*<<inc_prefix>>*//*<<component>>*///*<<component>>*/_core.h"
/*<<state_struct_def>>*/
/*<<csym>>*/_state_t *
/*<<create_name>>*/(/*<<create_params>>*/)
{
    /*<<csym>>*/_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
/*<<create_assignments>>*/
    return obj;
}

/*<<destroy_c_ret>>*/
/*<<csym>>*/_destroy(/*<<csym>>*/_state_t *state)
{
/*<<destroy_impl>>*/    free(state);/*<<destroy_ret_stmt>>*/
}
/*<<reset_c_open>>*//*<<reset_assignments>>*//*<<reset_c_close>>*/
/*<<steps_c_impl>>*/

/*<<getter_setter_impls>>*//*<<serializable_impls>>*/
