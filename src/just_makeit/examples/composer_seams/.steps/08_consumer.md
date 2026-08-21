## 8. The point: a C consumer needs only the generated header

```{08_consumer.c}
```

```sh
cc -I native/inc native/tests/test_bridge.c \
   build/native/src/backing/CMakeFiles/backing_core.dir/*.o \
   build/native/src/clip/CMakeFiles/clip_core.dir/*.o -lm -o /tmp/test_bridge
/tmp/test_bridge
# bridge consumer: PASSED
```

This is what the seams' prototypes moving out of `_ext.c` bought. That file is
a CPython translation unit — including it from a test is not an option — so
while the declarations lived there, a C test wanting to assert that the
composed path and the standalone path agree could reach only the half with a
public header. The real instance downstream had to say so in a comment and
cover the other half from Python instead, which is a weaker claim than the one
it was trying to make.

One header, one declaration each, checked by the compiler on both sides.
