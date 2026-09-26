/**
 * @file /*<<component>>*/_core.h
 * @brief /*<<Component>>*/ component API.
 *
 * Lifecycle: /*<<lifecycle_summary>>*/
 *
 * Example:
 * @code
/*<<create_example_c>>*//*<<step_example_c>>*/ * /*<<csym>>*/_destroy(obj);
 * @endcode
 */
#ifndef /*<<CSYM>>*/_CORE_H
#define /*<<CSYM>>*/_CORE_H

#include "/*<<inc_prefix>>*/clib_common.h"
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
 * @note Caller must call /*<<csym>>*/_destroy() when done.
 */
/*<<create_decl>>*/

/**
 * @brief Destroy a /*<<component>>*/ instance and release all memory.
 * @param state  May be NULL./*<<destroy_ret_doc>>*/
 */
/*<<destroy_decl>>*/

/*<<builtin_reset_decl>>*/

/*<<getter_setter_decls>>*//*<<serializable_decls>>*/

/*<<property_decls>>*/
/*<<method_decls>>*/
/*<<step_impl_def>>*/

/*<<steps_c_decl>>*/
/*<<inline_core>>*/
#ifdef __cplusplus
}
#endif

#endif /* /*<<CSYM>>*/_CORE_H */
