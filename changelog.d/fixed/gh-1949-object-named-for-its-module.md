- **An object named for its dotted module's leaf configures and builds;
    one named for the module's cname is the module's collocated object**
    (gh-1949). `jm module dsp.filters`, then
    `jm object filters --module dsp.filters`, exited 0 and left a project
    that `cmake` refused to configure, because `filters_core` was declared
    twice. An object's C lives in `native/src/<obj>/`, so only an object
    named for the module's cname (`dsp_filters`) shares the module's
    directory and core. The module's CMakeLists asked the leaf instead. It
    declared the core, C test and bench of `filters` a second time, beside
    the copies in the object's own directory, and it dropped the module's
    own `dsp_filters_core`, which the root CMakeLists still named.
    `jm apply` asked a third spelling, the dotted id, which no object name
    can equal. So a collocated `dsp_filters` object went STALE as soon as
    it carried include directories: `apply` added them a second time to the
    module's CMakeLists. Every writer now asks one rule,
    `collocated_object`: the object named for the module's cname. A flat
    module's leaf, cname and id are the same string, so a flat module's
    tree is unchanged.
