# The math library (libm), found as a full path. A bare `m` could be mistaken
# for a target of the same name (a module called `m`). Empty where the math
# functions are part of the C library itself (Windows, macOS).
find_library(JM_MATH_LIBRARY m)
if(NOT JM_MATH_LIBRARY)
  set(JM_MATH_LIBRARY "")
endif()
