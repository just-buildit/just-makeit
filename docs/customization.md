# Customizing your project

The generated project is a starting point. Here is the typical workflow for
extending it.

---

## 1. Declare your state variables upfront

Use `--state name:type` when running `init` so the scaffolding matches your
component from the start:

```sh
just-makeit init my_filter \
    --state cutoff_freq:double \
    --state num_taps:int
```

This generates the struct, constructor parameters, and getter/setter pairs
for each variable in one shot — no manual search-and-replace needed.

---

## 2. Implement `step`

Open `native/inc/<component>/<component>_core.h` and replace the pass-through
stub with your DSP logic:

```c
static inline float complex
my_filter_step(const my_filter_state_t *state, float complex x)
{
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}
```

Reads `state->cutoff_freq`, `state->num_taps`, etc. to process `x`.  The
function is `static inline` for maximum performance in the hot path.

---

## 3. Add more state variables

If you need variables not specified at `init` time, extend the struct in
`native/inc/<component>/<component>_core.h`:

```c
typedef struct {
    double cutoff_freq;
    int    num_taps;
    float  coeffs[64];      // add more fields here
    float  delay_line[64];
} my_filter_state_t;
```

Then implement the corresponding functions in `<component>_core.c` and
expose any new getters/setters in `<component>_ext.c`.

---

## 4. Expose new methods

Add new C functions to the header, implement them in the `.c` file, then
expose them in `<component>_ext.c`.

Each Python method follows this skeleton:

```c
static PyObject *
MyFilter_my_method(MyFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    // parse args, call C function, return result
}
```

Add an entry to `MyFilter_methods[]`:

```c
{"my_method", (PyCFunction)MyFilter_my_method, METH_VARARGS,
 "Brief description."},
```

Update the type stub `src/<package>/<component>.pyi` to match.

---

## 5. Add CTest tests

`native/tests/test_<component>_core.c` already has a template test. Add more:

```c
static void test_my_feature(void)
{
    my_filter_state_t *obj = my_filter_create(0.1, 32);
    float complex y = my_filter_step(obj, 1.0f + 0.0f * I);
    // assert ...
    my_filter_destroy(obj);
}

int main(void)
{
    test_my_feature();
    printf("all tests passed\n");
    return 0;
}
```

CTest runs all executables registered with `add_test()` in `CMakeLists.txt`.
Add more executables and `add_test()` entries for larger test suites.

---

## 6. Add dependencies

If your C code needs a third-party library (FFTW, libsndfile, etc.), link it
in `CMakeLists.txt`:

```cmake
find_package(FFTW3f REQUIRED)
target_link_libraries(<component>_core PRIVATE FFTW3::fftw3f)
```

For Python runtime dependencies, add them to `pyproject.toml`:

```toml
[project]
dependencies = [
    "numpy",
    "scipy",
]
```

---

## 7. Add a sibling component

Duplicate the `native/src/<component>/` and `native/inc/<component>/` trees
for the new component, add a new `Python3_add_library` target in
`CMakeLists.txt`, and update the `just-build` Makefile target to copy the
new `.so` into `src/<package>/`.
