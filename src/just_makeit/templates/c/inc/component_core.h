/**
 * @file /*<<component>>*/_core.h
 * @brief /*<<Component>>*/ component API.
 *
 * Lifecycle: create -> [step / steps/*<<lifecycle_reset>>*/]* -> destroy
 *
 * Example:
 * @code
 * /*<<component>>*/_state_t *obj = /*<<create_name>>*/(/*<<c_create_args>>*/);
 * /*<<step_example_lhs>>*//*<<component>>*/_step(obj/*<<step_example_suffix>>*/);
 * /*<<component>>*/_destroy(obj);
 * @endcode
 */
#ifndef /*<<COMPONENT>>*/_CORE_H
#define /*<<COMPONENT>>*/_CORE_H

#include "clib_common.h"
/*<<perf_include>>*//*<<depends_includes>>*/
#ifdef __cplusplus
extern "C" {
#endif

/*<<state_struct_decl>>*/

/**
 * @brief Create a /*<<component>>*/ instance.
 *
/*<<create_param_docs>>*/
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call /*<<component>>*/_destroy() when done.
 */
/*<<create_decl>>*/

/**
 * @brief Destroy a /*<<component>>*/ instance and release all memory.
 * @param state  May be NULL./*<<destroy_ret_doc>>*/
 */
/*<<destroy_decl>>*/

/*<<builtin_reset_decl>>*/

/*<<step_impl_def>>*/

/*<<steps_c_decl>>*/

/*<<getter_setter_decls>>*/

/*<<property_decls>>*/
/*<<method_decls>>*//*<<inline_core>>*/
#ifdef __cplusplus
}
#endif

#endif /* /*<<COMPONENT>>*/_CORE_H */
