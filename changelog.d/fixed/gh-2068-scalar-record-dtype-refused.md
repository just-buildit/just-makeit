- **`--record-dtype` naming a scalar element is refused, and writes
    nothing** (gh-2068). `record_dtype` is a struct's spelling: the kernel
    fills `<name> *out`. A scalar element's name is jm's alias, which no C
    type carries, so `jm method o read --borrow --record-dtype s` over
    `jm record o s --type double` wrote `s *p_o_read(...)`, exited 0, and
    the core did not compile (`unknown type name 's'`). `jm method` now
    refuses it before it writes anything, `apply` and `status` refuse the
    same row in a manifest, and `just-makeit record` refuses declaring a
    scalar under a member that already reads the name that way. The
    message names the scalar's spelling: `--return-type s`.
