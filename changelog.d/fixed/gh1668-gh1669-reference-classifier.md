- **`jm upgrade` respells what refers to a derived name, and nothing else**
    (gh-1668, gh-1669). Moving onto a `c_prefix` renamed every identifier
    spelled like a derived name: component `frame`'s method `bits` derives
    `frame_bits`, so a public struct field, a parameter and a local of that
    spelling were renamed too -- an API break that still compiled -- and
    reverting them tripped `apply`'s refusal. The other way, a call to your
    own macro that token-pastes a stem (`pfx##_state_bytes`) kept the old
    stem and the tree stopped building. One classifier in `_csym` now
    answers "is this a reference?" for both the respell and `apply`'s
    refusal: members, member accesses, designated initializers, parameters
    and locals keep their spelling; a stem passed to any pasting macro in
    the project moves like `JM_DEFINE_STEPS`'s; and a macro that pastes one
    argument into a derived name AND one of yours is refused, naming the
    call.
