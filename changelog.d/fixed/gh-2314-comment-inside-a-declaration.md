- **A comment inside a C declaration no longer hides the code before it**
    (gh-2314). jm read where a declaration in a `_core.h` begins by cutting
    at the last `*/` before its name, so `void /* none */ x_set_alpha(...)`
    read as returning nothing at all and gh-2302's check refused a setter
    that returns `void`; and `static inline float /* hot */` above the step
    split the step's declaration, so the next prototype jm inserted landed
    between the return type and the name. Both now read the header with its
    comments blanked, back to the previous statement, brace or preprocessor
    line, and a type that cannot be read is no finding.
