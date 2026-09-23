# Binding a C enum whose values are not 0..n-1

A real C API rarely numbers its enum `0, 1, 2, …`. A sentinel sits at `-1`,
levels are spaced by ten, a first value starts at `1`. This example binds one:

```c
typedef enum {
    LVL_AUTO = -1,  ///< Let the gate choose (it picks `info`).
    LVL_DEBUG = 10, ///< Diagnostic detail.
    LVL_INFO = 20,  ///< Normal operation.
    LVL_WARN = 30,  ///< Something needs attention.
} gate_level_t;
```

Python sees the strings `"auto"`, `"debug"`, `"info"` and `"warn"`, and C
receives the constants. That holds on every path a choice crosses: a
constructor argument, a property, and a method argument.

## 1. Scaffold

```sh
jm new levels --object gate --no-step --state threshold:int:20
cd levels
```

`20` is `LVL_INFO`'s value. jm writes a state default into the C and into
the scaffolded Python test, and a C constant's name means nothing in the
Python.

## 2. Declare the enum, and use it

In `just-makeit.toml`, name the choices and, in the same order, the C
constant each one means:

```toml
[[enum]]
name = "level"
values = ["auto", "debug", "info", "warn"]
enumerators = ["LVL_AUTO", "LVL_DEBUG", "LVL_INFO", "LVL_WARN"]
```

Then use it from `objects/gate.toml`, three ways:

```toml
[[gate.init_params]]          # Gate(level="auto")
name = "level"
type = "enum:level"
default = "auto"

[[gate.properties]]           # g.threshold, read and written as a string
name = "threshold"
type = "int"
field = true
writable = true
enum = "level"

[[gate.methods]]              # g.passes("warn")
name = "passes"
return_type = "int"
params = [{ name = "level", type = "int", enum = "level" }]
```

The constructor now takes `level`. The Python test `jm new` scaffolded
still carries its `# jm:generated` line, so it is jm's, and `apply` renders
it again for the new constructor:

```sh
jm apply
```

Every table jm generates spells the constants **by name**:

```c
static const int _enum_Gate_level_c[] = {
    LVL_AUTO,
    LVL_DEBUG,
    LVL_INFO,
    LVL_WARN,
};
```

So the C compiler checks each one. If a constant is renamed or deleted, the
build fails. It can't quietly start meaning a different choice.

Without `enumerators`, a choice's **position** is its C value, as it always
was. That's right for an enum numbered `0..n-1` in declaration order, and
wrong for this one. In this example, `"debug"` would reach C as `1`.

## 3. Write the C, and re-apply

The enum goes in the sacred header, above the state struct. The kernels go in
`native/src/gate/gate_core.c`:

```c
gate_state_t *
gate_create(int level)
{
    ...
    obj->threshold = level == LVL_AUTO ? LVL_INFO : level;
    ...
}

int
gate_passes(gate_state_t *state, int level)
{
    return level >= state->threshold;
}
```

```sh
jm apply           # picks up each constant's ///< doc into the stub
jm status --check
```

The same `enumerators` list tells jm where each choice's documentation
lives. After the second `apply`, `help(Gate)` lists every level with its
description:

```text
level : Literal["auto", "debug", "info", "warn"], default "auto"
    - ``"auto"`` — Let the gate choose (it picks `info`).
    - ``"debug"`` — Diagnostic detail.
    ...
```

## 4. Build, and cross every path

```sh
cmake -B build -S . && cmake --build build && ctest --test-dir build
```

```python
from levels import Gate

g = Gate()                 # "auto" -> LVL_AUTO (-1); C resolves it to info
assert g.threshold == "info"

g = Gate(level="debug")    # LVL_DEBUG: 10, not index 1
g.threshold = "warn"       # the setter stores LVL_WARN, 30
assert g.passes("info") == 0 and g.passes("warn") == 1

g.threshold = "auto"       # -1 is legal, in both directions
assert g.threshold == "auto"

g.passes("verbose")        # ValueError: invalid level 'verbose'
                           #   (choices: auto, debug, info, warn)
```

`-1` works in both directions. A string is looked up as its **index** first,
and only that index can signal "not found". The constant is read from the
table after that, so no C value is ever mistaken for a miss. Going the other
way, a value that is none of the constants raises `ValueError` rather than
reading past the table.
