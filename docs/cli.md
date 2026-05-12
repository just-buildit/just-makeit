```bash
[user@pc] $ just-makeit --help

Usage: just-makeit <command> [options]

Commands:
  new <proj> [dir] [OPTIONS]    Create a new project scaffold.
    --object name               Also scaffold a standalone object.
    --module name               Also scaffold an extension module; repeatable.
    --state name:type[:default] Initial state variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --basic                     Emit a plain Makefile instead of CMake.

  module <name>                 Add an extension module subpackage to a project.

  object <name> [OPTIONS]       Add a Python-wrapped C type to a project.
    --module name               Place object inside this module's .so.
    --state name:type[:default] State variable; repeatable.
    --arg-type TYPE             step() input type (default: float _Complex).
    --return-type TYPE          step() return type (default: --arg-type).
    --perf                      Annotate step() with JM_HOT/JM_FORCEINLINE.
    --no-state                  Generate empty state struct; user fills in fields manually.
    --no-step                   Omit step() method.

  method <obj> <name> [OPTIONS] Add a named execute variant to an object.
    --module name               Module the object lives in.
    --param name:type           Input parameter; repeatable.
    --arg-type TYPE             Bulk-input array type.
    --return-type TYPE          Return type.
    --variable-output           Output length determined at runtime.
    --multi-output TYPE         Emit a second output array of this type.

  property <obj> <name> [OPTIONS]  Add a Python property to an object.
    --module name               Module the object lives in.
    --type TYPE                 C type of the property value.
    --writable                  Generate a setter in addition to the getter.
    --field                     Back property with a struct field (no getter C fn).

  function <name> [OPTIONS]     Add a module-level C function.
    --module name               Module to add the function to (required).
    --param name:type           Input parameter; repeatable.
    --return-type TYPE          Return type (default: void).
    --doc "text"                Docstring shown in Python help().

  add [OPTIONS]                 Append variables to the current object.
    --state name:type[:default] Add a state variable.
    --param name:type[:default] Add a constructor parameter.

  perf                          Retrofit JM_HOT/JM_FORCEINLINE without touching user code.
  config [key value]            Show all config keys, or get/set one value.
  build [dir]                   Build C extensions and package a wheel (default: dist/).
  test                          Build then run CTest + pytest.
  dry-run                       Show what would be compiled without building.
  install-deps [path]           Install cmake, C compiler, numpy, and create a venv.
  example [name]                Run a bundled end-to-end example (omit name to list).
  help                          Show this message.

Types (--arg-type / --return-type / --param / --state):
  void  float  double  float _Complex  double _Complex
  int  int8_t…int64_t  uint8_t…uint64_t  size_t  ptrdiff_t
  Append [] for array params: float _Complex[]  int16_t[]  …
  Append [N] for fixed-length state fields: float[64]  double _Complex[32]

Examples:
  just-makeit new my_filter                                # project scaffold only
  just-makeit new my_filter --object my_filter            # project + first object
  just-makeit new my_bpf --object bpf --state center:double --state bw:double
  just-makeit new my_filters --module filter              # project + one module
  just-makeit new my_dsp --module osc --module env        # project + two modules
  just-makeit object sink --arg-type "float _Complex" --return-type void  # sink object
  just-makeit object gen  --arg-type void --return-type "float _Complex"  # generator
  just-makeit object engine --state rate:double:1.0       # standalone stateful object
  just-makeit object norm --state scale:double:1.0        # object with one state var
  just-makeit object fir --module filter                  # object in a module
  just-makeit method nco configure --module dsp \
      --param freq:float --param phase:float --return-type void
  just-makeit method resamp execute_ctrl --module dsp \
      --param ctrl:"float _Complex[]" --return-type size_t
  just-makeit method nco execute_cf32 --module dsp \
      --arg-type void --return-type "float _Complex" --variable-output
  just-makeit method nco execute_u32_ovf --module dsp \
      --arg-type void --return-type uint32_t --variable-output --multi-output uint8_t
  just-makeit function apply_window --module fft \
      --param data:"float _Complex[]" --return-type void
  just-makeit property nco phase --module dsp --type uint32_t
  just-makeit property buffer dropped --type size_t
  just-makeit add --state order:int:4                     # add state var
  just-makeit add --state n_taps:int:16
  just-makeit config                                      # show project config
  just-makeit config version 0.2.0                        # set version
  just-makeit build                                       # build wheel into dist/
  just-makeit test                                        # run all tests
  just-makeit dry-run                                     # preview build plan
  ```