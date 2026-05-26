#include "/*<<component>>*///*<<component>>*/_core.h"

/*<<component>>*/_state_t *
/*<<component>>*/_create(/*<<create_params>>*/)
{
    /*<<component>>*/_state_t *state = calloc(1, sizeof(*state));
    if (!state)
        return NULL;
/*<<create_assignments>>*/
    return state;
}

void
/*<<component>>*/_destroy(/*<<component>>*/_state_t *state)
{
/*<<destroy_impl>>*/    free(state);
}

void
/*<<component>>*/_reset(/*<<component>>*/_state_t *state)
{
/*<<reset_assignments>>*/
}

/*<<steps_c_impl>>*/

/*<<getter_setter_impls>>*/
