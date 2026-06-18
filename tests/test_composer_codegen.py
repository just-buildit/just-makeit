"""Codegen tests for ``kind = "composer"`` modules (gh-287, C2.2.a).

Render the enum tables and the source ``PyTypeObject`` (e.g. ``Synth``) for a
composer module and assert the generated C has the right shape. The full
compile + behavior parity is exercised against doppler's real ``wfm_source_t``
in the pilot; this keeps the structure under a compiler-free unit gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _composer


def _cfg():
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [
            {
                "name": "wfm_type",
                "values": ["tone", "noise", "pn", "bpsk", "qpsk"],
            },
            {"name": "snr_mode", "values": ["auto", "fs", "ebno", "esno"]},
            {"name": "lfsr", "values": ["galois", "fibonacci"]},
        ],
        "module": {
            "wfm_compose": {
                "kind": "composer",
                "backing": "wfm_compose",
                "capsule_name": "doppler.wfm.compose_state",
                "package": "wfm",
                "composes": ["wfm_synth"],
                "source": {
                    "object": "wfm_synth",
                    "struct": "wfm_source_t",
                    "type_name": "Synth",
                    "fields": [
                        {
                            "name": "type",
                            "type": "int",
                            "enum": "wfm_type",
                            "default": "tone",
                        },
                        {"name": "freq", "type": "double", "default": "0.0"},
                        {
                            "name": "snr_mode",
                            "type": "int",
                            "enum": "snr_mode",
                            "default": "auto",
                        },
                        {"name": "seed", "type": "uint32_t", "default": "1"},
                        {
                            "name": "pn_poly",
                            "type": "uint64_t",
                            "default": "0",
                        },
                        {
                            "name": "lfsr",
                            "type": "int",
                            "enum": "lfsr",
                            "default": "galois",
                        },
                        {"name": "bits", "type": "uint8_t*", "bytes": True},
                    ],
                },
                "segment": {
                    "type_name": "Segment",
                    "struct": "wfm_segment_t",
                    "fields": [
                        {"name": "fs", "type": "double", "default": "1e6"},
                        {
                            "name": "num_samples",
                            "type": "size_t",
                            "default": "1024",
                        },
                        {
                            "name": "off_samples",
                            "type": "size_t",
                            "default": "0",
                        },
                    ],
                    "sources": "multi",
                },
                "timeline": {
                    "type_name": "Timeline",
                    "loop": ["once", "repeat", "continuous"],
                },
                "oo": {
                    "factories": ["tone", "qpsk"],
                    "emit": "ctypes",
                    "discriminant": "type",
                    "composer_type_name": "Composer",
                },
                "json": {
                    "enabled": True,
                    "to_json_fn": "wfm_spec_to_json",
                    "to_json_trailing": ["0.0"],
                },
            }
        },
    }


class TestEnumTables:
    def test_only_referenced_enums_emitted(self):
        s = _composer.render_enum_tables(_cfg(), "wfm_compose")
        # wfm_type / snr_mode / lfsr are referenced; the table order is the SSOT
        assert "static const char *const _enum_wfm_type[] = {" in s
        assert "static const char *const _enum_snr_mode[] = {" in s
        assert "static const char *const _enum_lfsr[] = {" in s
        assert "_enum_index(const char *const *tab, const char *s)" in s
        # values in declared order, NULL-terminated
        idx_tone = s.index('"tone"')
        idx_qpsk = s.index('"qpsk"')
        assert idx_tone < idx_qpsk

    def test_unreferenced_enum_skipped(self):
        cfg = _cfg()
        cfg["enum"].append({"name": "unused", "values": ["x", "y"]})
        s = _composer.render_enum_tables(cfg, "wfm_compose")
        assert "_enum_unused" not in s


class TestSourceType:
    def test_struct_and_type_object(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "wfm_source_t src;" in s
        assert "double   fs;" in s
        assert "static PyTypeObject SynthType =" in s
        assert '.tp_name      = "doppler.wfm.Synth"' in s
        assert "(initproc)Synth_init" in s
        assert "(destructor)Synth_dealloc" in s

    def test_init_keyword_parse(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        # kwlist has every field + fs
        assert (
            '"type", "freq", "snr_mode", "seed", "pn_poly", "lfsr", "bits", "fs"'
            in s
        )
        # enum field validated into the struct int
        assert "_enum_index(_enum_wfm_type, type)" in s
        assert "self->src.type = _i;" in s
        # scalar assigned directly
        assert "self->src.freq = freq;" in s
        # bytes attached
        assert "_attach_bytes(&self->src, bits)" in s

    def test_getset_enum_as_string(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "PyUnicode_FromString(_enum_wfm_type[self->src.type])" in s
        # writable enum setter validates
        assert "Synth_set_snr_mode" in s
        # scalar getter coerces
        assert "PyLong_FromUnsignedLongLong" in s  # pn_poly (uint64)

    def test_dealloc_frees_bits(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "free(self->src.bits);" in s

    def test_factories(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert 'return _Synth_factory("tone", args, kwds);' in s
        assert 'return _Synth_factory("qpsk", args, kwds);' in s
        # the discriminant is injected
        assert 'PyDict_SetItemString(k, "type", v)' in s

    def test_factory_method_rows(self):
        rows = _composer.factory_method_rows(_cfg(), "wfm_compose")
        joined = "\n".join(rows)
        assert '{"tone", (PyCFunction)(void (*)(void))_factory_tone' in joined
        assert "METH_VARARGS | METH_KEYWORDS" in joined


class TestSegmentType:
    def test_struct_and_type_object(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        # holds a sources list + the segment scalar fields (no backing struct)
        assert "PyObject *sources;" in s
        assert "double fs;" in s
        assert "size_t num_samples;" in s
        assert "size_t off_samples;" in s
        assert "static PyTypeObject SegmentType =" in s
        assert '.tp_name      = "doppler.wfm.Segment"' in s
        # forward declaration so sum() can allocate before the type is defined
        assert "static PyTypeObject SegmentType;" in s

    def test_inline_ctor_forwards_to_source_type(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        # builds one source by forwarding leftover args/kwds to the source type
        assert "PyObject_Call((PyObject *)&SynthType, args, kw)" in s
        # segment fields are popped (deleted) from the forwarded kwds
        assert 'PyDict_DelItemString(kw, "num_samples")' in s

    def test_sum_classmethod(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        assert "Segment_sum(PyObject *cls" in s
        assert "METH_VARARGS | METH_KEYWORDS | METH_CLASS" in s
        # validates each positional source is the source type
        assert "PyObject_TypeCheck(it, &SynthType)" in s
        assert "needs at least one source" in s
        # allocates via the (forward-declared) type object
        assert "SegmentType.tp_alloc(&SegmentType, 0)" in s

    def test_getsets_and_dealloc(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        assert "Segment_get_sources" in s  # read-only sources getter
        assert "Segment_get_num_samples" in s
        assert "Segment_set_fs" in s
        assert "Py_XDECREF(self->sources);" in s
        # warning-clean keyword-method cast
        assert "(PyCFunction)(void (*)(void))Segment_sum" in s


class TestTimelineType:
    def test_sequence_protocol(self):
        s = _composer.render_timeline_type(_cfg(), "wfm_compose")
        assert "static PyTypeObject TimelineType =" in s
        assert '.tp_name      = "doppler.wfm.Timeline"' in s
        assert "Timeline_iter" in s and ".tp_iter" in s
        assert ".mp_length" in s and ".mp_subscript" in s
        # add() extends and returns self (chainable)
        assert "Timeline_add" in s
        assert "PyList_Append(self->segments" in s
        # init copies any iterable
        assert "PySequence_List(seq)" in s


class TestComposerType:
    def test_holds_backing_state(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "wfm_compose_state_t *state;" in s
        assert "int                destroyed;" in s
        assert "static PyTypeObject ComposerType =" in s
        assert '.tp_name      = "doppler.wfm.Composer"' in s

    def test_build_segments_from_oo(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        # builds a transient wfm_segment_t[] from Segment/Synth objects
        assert "_build_wfm_compose_segments" in s
        assert "srcs[k] = syn->src;" in s  # aliases bits, create deep-copies
        assert "segs[i].sources = srcs;" in s
        assert "segs[i].n_sources = (size_t)ns;" in s
        assert "segs[i].fs = seg->fs;" in s
        # frees only the transient arrays, never the bits (owned by Synth)
        assert "NOT the bits" in s
        assert "wfm_compose_create(segs, n, repeat, continuous)" in s

    def test_init_dispatch(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        # single-segment-from-kwargs vs explicit segments vs Timeline/list
        assert "PyObject_Call((PyObject *)&SegmentType, empty, kw)" in s
        assert "PyObject_TypeCheck(segments, &SegmentType)" in s
        assert "PySequence_List(segments)" in s
        assert "not both" in s  # segments XOR kwargs guard
        assert '_pop_flag(kw, "repeat"' in s

    def test_execute_and_compose(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "wfm_compose_execute(self->state, out, (size_t)max)" in s
        assert "Py_BEGIN_ALLOW_THREADS" in s  # GIL released across the kernel
        assert "PyArray_Concatenate(chunks, 0)" in s
        assert "cannot compose() a continuous spec" in s

    def test_resolved_and_close(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        # segments/repeat/continuous reflect the resolved spec as OO objects
        assert "_wfm_compose_segments_to_list" in s
        assert "wfm_compose_segments(self->state" in s
        assert "Composer_get_segments" in s
        # rebuilt sources deep-copy their bits
        assert "malloc(syn->src.n_bits)" in s
        # close / context manager / dealloc destroy the backing state
        assert "wfm_compose_destroy(self->state)" in s
        assert "Composer_enter" in s and "Composer_exit" in s


class TestSegmentAdd:
    def test_add_returns_timeline(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        # forward-declares the Timeline type it sequences into
        assert "static PyTypeObject TimelineType;" in s
        assert "Segment_add" in s
        # builds [self, *others] and constructs a Timeline
        assert "PyList_SET_ITEM(list, 0, (PyObject *)self);" in s
        assert (
            "PyObject_CallFunctionObjArgs((PyObject *)&TimelineType, list, NULL)"
            in s
        )
        assert '"add", (PyCFunction)Segment_add' in s

    def test_add_omitted_without_timeline(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"].pop("timeline")
        s = _composer.render_segment_type(cfg, "wfm_compose")
        assert "Segment_add" not in s
        assert "TimelineType;" not in s


class TestComposerJson:
    def test_json_methods(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        # forward decl so from_json/from_file can allocate the type
        assert "static PyTypeObject ComposerType;" in s
        # from_json/from_file are classmethods; to_json is an instance method
        assert "Composer_from_json" in s and "Composer_from_file" in s
        assert "Composer_to_json" in s
        assert "wfm_compose_from_json(json)" in s
        assert "wfm_compose_from_file(PyBytes_AS_STRING(pathobj))" in s
        assert "PyUnicode_FSConverter" in s  # accepts str + PathLike
        # irregular serializer name + trailing headroom arg are manifest-driven
        assert "wfm_spec_to_json(segs, n, repeat, continuous, 0.0)" in s
        assert "METH_VARARGS | METH_CLASS" in s

    def test_json_omitted_when_disabled(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"].pop("json", None)
        s = _composer.render_composer_type(cfg, "wfm_compose")
        assert "Composer_from_json" not in s
        assert "Composer_to_json" not in s

    def test_init_keeps_seglist_alive_until_create(self):
        # gh-287 UAF fix: the transient segs alias the Synth bits buffers, so
        # seglist must outlive create (which deep-copies). DECREF comes AFTER.
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        create = s.index("wfm_compose_create(segs, n, repeat, continuous)")
        free = s.index("_free_wfm_compose_segments(segs, n);")
        decref = s.index("Py_DECREF(seglist);", create)  # the post-build one
        assert create < free < decref
        assert "seglist must outlive" in s


class TestComposerJsonGenerated:
    def _gen_cfg(self):
        # drop the to_json_fn delegation -> generic SSOT-driven generated ser/de
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["json"] = {"enabled": True}
        return cfg

    def test_generated_by_default(self):
        s = _composer.render_composer_type(self._gen_cfg(), "wfm_compose")
        # generated path: builds segs from a parsed cJSON root, enum via SSOT
        assert "_json_parse_source" in s and "_json_add_source" in s
        assert "_wfm_compose_from_root" in s
        # enum serialized via the SSOT table (not a duplicated table)
        assert (
            'cJSON_AddStringToObject(so, "type", _enum_wfm_type[src->type])'
            in s
        )
        assert "_enum_index(_enum_wfm_type" in s  # parse side
        # uniform schema: a sources array, version stamped from the module name
        assert 'cJSON_AddArrayToObject(sj, "sources")' in s
        assert '"wfm_compose-1"' in s
        # bytes field <-> JSON int array
        assert "cJSON_CreateNumber(src->bits[bi])" in s
        # NOT delegating to a hand serializer
        assert "wfm_spec_to_json" not in s

    def test_delegation_when_to_json_fn_set(self):
        # the default _cfg() sets json.to_json_fn -> delegation escape hatch
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "wfm_spec_to_json(segs, n, repeat, continuous, 0.0)" in s
        assert "_json_parse_source" not in s  # no generated ser/de

    def test_render_ext_includes_cjson_only_when_generated(self):
        gen = _composer.render_ext(self._gen_cfg(), "wfm_compose")
        assert '#include "cJSON.h"' in gen and "#include <stdio.h>" in gen
        deleg = _composer.render_ext(_cfg(), "wfm_compose")
        assert "cJSON.h" not in deleg

    def test_cmake_adds_json_include_dir(self):
        cfg = self._gen_cfg()
        cfg["module"]["wfm_compose"]["json"]["include_dir"] = (
            "${CMAKE_SOURCE_DIR}/vendor/cjson"
        )
        cm = _composer.render_cmake(cfg, "wfm_compose")
        assert "${CMAKE_SOURCE_DIR}/vendor/cjson" in cm


class TestComposerCli:
    def _cli_cfg(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["cli"] = {
            "enabled": True,
            "name": "wfmgen2",
        }
        return cfg

    def test_render_cli_shape(self):
        s = _composer.render_cli(self._cli_cfg(), "wfm_compose")
        assert "int\nmain(int argc, char **argv)" in s
        # reuses jm app's output-axes machinery
        assert "jm_write_block(" in s and "jm_convert_block(" in s
        assert "--sample_type" in s and "--file-type" in s and "--endian" in s
        # source-field flags; enum validated via the SSOT table (no hand table)
        assert '"--type"' in s and '"--freq"' in s
        assert "_enum_index(_enum_wfm_type, type)" in s
        # segment-field flag + --from-file (backing parser) + build/exec/destroy
        assert '"--num_samples"' in s and '"--from-file"' in s
        assert "wfm_compose_create(&seg, 1, repeat, continuous)" in s
        assert "wfm_compose_execute(c, buf, 4096)" in s
        # pure C tool — no Python
        assert "Python.h" not in s and "PyObject" not in s
        # bytes-field buffer freed after create (create deep-copies) — no leak
        assert "free(src.bits);" in s

    def test_cli_cmake_exe_outside_build_python(self):
        cfg = self._cli_cfg()
        cm = _composer.render_cmake(cfg, "wfm_compose")
        # the exe block follows the endif() that closes the BUILD_PYTHON guard
        assert "add_executable(wfmgen2 wfm_compose_cli.c)" in cm
        assert cm.index("endif()") < cm.index("add_executable(wfmgen2")

    def test_cli_absent_by_default(self):
        cm = _composer.render_cmake(_cfg(), "wfm_compose")
        assert "add_executable" not in cm
        assert _composer.composer_cli(_cfg(), "wfm_compose") == {}


class TestSubclassable:
    """All four OO types set ``Py_TPFLAGS_BASETYPE`` so a project can subclass
    them — e.g. wrap the generated ``Synth`` in a Python subclass that adds
    standalone-generation conveniences while still flowing through the generated
    ``Composer`` (which type-checks with subclass-accepting ``PyObject_TypeCheck``).
    Without the flag, CPython rejects subclassing ("not an acceptable base type").
    """

    def test_source_type_is_basetype(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE" in s

    def test_segment_type_is_basetype(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        assert "Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE" in s

    def test_timeline_type_is_basetype(self):
        s = _composer.render_timeline_type(_cfg(), "wfm_compose")
        assert "Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE" in s

    def test_composer_type_is_basetype(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE" in s

    def test_all_four_types_in_full_ext(self):
        """The assembled module sets the flag on every emitted type."""
        s = _composer.render_ext(_cfg(), "wfm_compose")
        assert s.count("Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE") == 4


def _gen_cfg():
    """_cfg() with the source declaring standalone generation (feature 1)."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["source"]["generates"] = {
        "generator": "wfm_synth",
        "bridge_fn": "wfm_source_to_synth",
    }
    return cfg


class TestSourceGenerates:
    """``[module.X.source.generates]`` (gh-287 round 3): the source type
    generates standalone by delegating to a composed generator, built by the
    project's straight-C ``bridge_fn``. jm emits the steps/step/reset plumbing;
    the bridge is pure C. Generic — wfm_synth is just one instance."""

    def test_struct_holds_generator_handle(self):
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        assert "wfm_synth_state_t *_gen;" in s

    def test_emits_steps_step_reset(self):
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        assert "Synth_steps(SynthObject *self" in s
        assert "Synth_step(SynthObject *self" in s
        assert "Synth_reset(SynthObject *self" in s
        # delegates to the generator's variable-output steps and scalar step
        assert "wfm_synth_steps(self->_gen, out, (size_t)n)" in s
        assert "wfm_synth_step(self->_gen)" in s
        assert "wfm_synth_reset(self->_gen)" in s

    def test_lazy_build_via_bridge(self):
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        # the handle is built once by the project's straight-C bridge
        assert "wfm_source_to_synth(&self->src, self->fs)" in s
        assert "Synth_ensure_gen(SynthObject *self)" in s

    def test_dealloc_destroys_generator(self):
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        assert "if (self->_gen) wfm_synth_destroy(self->_gen);" in s

    def test_tp_methods_wired(self):
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        assert ".tp_methods   = Synth_methods," in s

    def test_init_reinit_safe(self):
        """A second __init__() must destroy any prior generator before clearing
        the handle (tp_alloc zero-inits the first construction, so a bare
        `_gen = NULL` would leak the built generator on re-init)."""
        s = _composer.render_source_type(_gen_cfg(), "wfm_compose")
        assert (
            "if (self->_gen) { wfm_synth_destroy(self->_gen); "
            "self->_gen = NULL; }" in s
        )

    def test_ext_includes_generator_header_and_bridge_decl(self):
        s = _composer.render_ext(_gen_cfg(), "wfm_compose")
        assert '#include "wfm_synth/wfm_synth_core.h"' in s
        assert (
            "extern wfm_synth_state_t *wfm_source_to_synth("
            "const wfm_source_t *, double);" in s
        )

    def test_absent_without_generates(self):
        """A source with no ``generates`` emits none of the generation glue."""
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "_gen" not in s
        assert "Synth_steps" not in s
        assert ".tp_methods" not in s

    def test_pyi_declares_generation_methods(self):
        pyi = _composer.render_pyi(_gen_cfg(), "wfm_compose")
        assert "def steps(self, n: int) -> NDArray[np.complex64]: ..." in pyi
        assert "def step(self) -> complex: ..." in pyi
        # absent without generates
        assert "def steps(" not in _composer.render_pyi(_cfg(), "wfm_compose")


def _alias_coerce_cfg():
    """_cfg() with a field alias (freq<-f_start) + bit_pattern coercion (bits)."""
    cfg = _cfg()
    for f in cfg["module"]["wfm_compose"]["source"]["fields"]:
        if f["name"] == "freq":
            f["aliases"] = ["f_start"]
        if f["name"] == "bits":
            f["aliases"] = ["pattern"]
            f["coerce"] = "bit_pattern"
    return cfg


class TestFieldAliases:
    """``aliases`` (feature 2a): a ctor kwarg stands in for the canonical field,
    folded before parsing; passing both errors. Generic — generated in tp_init."""

    def test_alias_fold_emitted(self):
        s = _composer.render_source_type(_alias_coerce_cfg(), "wfm_compose")
        assert 'PyDict_GetItemString(kwds, "f_start")' in s
        assert "freq and f_start are aliases" in s
        assert "_kw = PyDict_Copy(kwds)" in s
        # pattern -> bits alias too
        assert 'PyDict_GetItemString(kwds, "pattern")' in s
        # parse uses the (possibly copied) kw dict and frees it
        assert "PyArg_ParseTupleAndKeywords(args, _kw," in s
        assert "if (_kw_owned) Py_DECREF(_kw);" in s

    def test_no_alias_no_copy(self):
        """Without aliases, tp_init parses kwds directly (no copy, no _kw)."""
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "PyArg_ParseTupleAndKeywords(args, kwds," in s
        assert "_kw_owned" not in s


class TestBitPatternCoercion:
    """``coerce = "bit_pattern"`` (feature 2b): a bytes field accepts a 0/1
    pattern as bytes, a binary/hex string, or a sequence of ints — generated."""

    def test_coercion_paths_emitted(self):
        s = _composer.render_source_type(_alias_coerce_cfg(), "wfm_compose")
        assert "PyUnicode_Check(obj)" in s  # str path
        assert "PySequence_Fast(" in s  # sequence path
        assert "s[1] == 'x' || s[1] == 'X'" in s  # 0x hex
        assert "bit string must be 0/1 or '0x..' hex" in s

    def test_plain_bytes_only_without_coerce(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "bits must be bytes or None" in s
        assert "PyUnicode_Check" not in s


def _stream_cfg():
    """_cfg() with the composer declaring a generated ``stream()`` (feature 3)."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["composer"] = {"stream": True}
    return cfg


class TestComposerStream:
    """``[module.X.composer] stream`` (gh-287 round 3): a generated
    ``Composer.stream(block=4096)`` returning an internal iterator that drains
    ``execute`` into blocks — the ``for blk in c.stream(n):`` convenience, so a
    project drops its hand-written streaming wrapper. Generic — the iterator is
    defined purely in terms of the composer's own ``execute``."""

    def test_iterator_type_and_method_emitted(self):
        s = _composer.render_composer_type(_stream_cfg(), "wfm_compose")
        # an internal iterator type with the iter protocol slots
        assert "ComposerStreamObject" in s
        assert ".tp_iter      = ComposerStream_iter," in s
        assert ".tp_iternext  = (iternextfunc)ComposerStream_next," in s
        # the stream() method building it, wired into tp_methods
        assert "Composer_stream(ComposerObject *self" in s
        assert '{"stream", (PyCFunction)(void (*)(void))Composer_stream,' in s

    def test_iterator_drains_execute(self):
        s = _composer.render_composer_type(_stream_cfg(), "wfm_compose")
        # next() pulls a block from the composer's own execute …
        assert 'PyObject_CallMethod(self->composer, "execute", "n"' in s
        # … and an empty block ends iteration (StopIteration via NULL)
        assert "if (n == 0)" in s

    def test_block_guard_and_default(self):
        s = _composer.render_composer_type(_stream_cfg(), "wfm_compose")
        assert "Py_ssize_t block = 4096;" in s
        assert "block must be > 0" in s

    def test_iterator_holds_strong_ref(self):
        """The iterator pins the composer (its execute backs every block)."""
        s = _composer.render_composer_type(_stream_cfg(), "wfm_compose")
        assert "Py_INCREF(self);" in s
        assert "Py_XDECREF(self->composer);" in s

    def test_internal_type_readied_not_exposed(self):
        """PyType_Ready'd so instances are usable, but not module-added."""
        ext = _composer.render_ext(_stream_cfg(), "wfm_compose")
        assert "PyType_Ready(&ComposerStreamType)" in ext
        assert 'PyModule_AddObject(m, "ComposerStream"' not in ext

    def test_absent_by_default(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "ComposerStream" not in s
        assert "Composer_stream" not in s
        ext = _composer.render_ext(_cfg(), "wfm_compose")
        assert "ComposerStreamType" not in ext

    def test_pyi_declares_stream(self):
        pyi = _composer.render_pyi(_stream_cfg(), "wfm_compose")
        assert (
            "def stream(self, block: int = ...)"
            " -> Iterator[NDArray[np.complex64]]: ..." in pyi
        )
        assert "from typing import Any, Iterator" in pyi
        # absent without the table — and Iterator is not imported
        plain = _composer.render_pyi(_cfg(), "wfm_compose")
        assert "def stream(" not in plain
        assert "from typing import Any\n" in plain


def _flat_cfg():
    """_cfg() with the segment opting into flat single-source accessors (feat 4)."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["segment"]["flat_sources"] = True
    return cfg


class TestSegmentFlatAccessors:
    """``[module.X.segment] flat_sources`` (gh-287 round 3 feature 4): a Segment
    built from exactly one source proxies that source's fields as read-only
    attributes (``segment.freq`` → ``segment.sources[0].freq``), so a project
    drops a hand-written ``__getattr__`` fallback. Generic — the names come from
    ``source.fields`` and each getter delegates to the source's own getset."""

    def test_flat_getsets_emitted_for_source_fields(self):
        s = _composer.render_segment_type(_flat_cfg(), "wfm_compose")
        # one read-only getset per source field, delegating to sources[0]
        for n in (
            "type",
            "freq",
            "snr_mode",
            "seed",
            "pn_poly",
            "lfsr",
            "bits",
        ):
            assert f"Segment_flat_{n}(SegmentObject *self" in s
            assert (
                f'{{"{n}", (getter)Segment_flat_{n}, NULL, NULL, NULL}},' in s
            )
        assert (
            'PyObject_GetAttrString(PyList_GET_ITEM(self->sources, 0), "freq")'
            in s
        )

    def test_multi_source_raises_attributeerror(self):
        s = _composer.render_segment_type(_flat_cfg(), "wfm_compose")
        assert "if (PyList_GET_SIZE(self->sources) != 1)" in s
        assert "PyExc_AttributeError" in s

    def test_read_only_no_setter(self):
        """Flat accessors are read-only — the getset row has a NULL setter."""
        s = _composer.render_segment_type(_flat_cfg(), "wfm_compose")
        # the source-proxy rows pass NULL where the segment scalars pass a setter
        assert "(setter)Segment_flat_freq" not in s

    def test_segment_scalar_still_writable(self):
        """The segment's own scalars keep their read/write getset (not flat)."""
        s = _composer.render_segment_type(_flat_cfg(), "wfm_compose")
        assert "(setter)Segment_set_fs" in s
        assert "Segment_flat_fs" not in s  # fs is a segment field, not proxied

    def test_no_collision_with_segment_fields(self):
        """A source field that shares a segment field's name is not proxied —
        the segment's own attribute wins (generic collision guard)."""
        cfg = _flat_cfg()
        # give the source a field named "fs" (collides with the segment scalar)
        cfg["module"]["wfm_compose"]["source"]["fields"].append(
            {"name": "fs", "type": "double", "default": "0.0"}
        )
        s = _composer.render_segment_type(cfg, "wfm_compose")
        assert "Segment_flat_fs" not in s

    def test_absent_without_flat_sources(self):
        s = _composer.render_segment_type(_cfg(), "wfm_compose")
        assert "Segment_flat_" not in s

    def test_pyi_declares_flat_attributes(self):
        pyi = _composer.render_pyi(_flat_cfg(), "wfm_compose")
        assert "    freq: float" in pyi
        assert "    type: str" in pyi  # enum field → str
        assert "    bits: bytes | None" in pyi
        # absent without the table
        assert "    freq: float" not in _composer.render_pyi(
            _cfg(), "wfm_compose"
        )

    def test_generic_non_wfm_composer(self):
        """Proves the engine is a generic object-of-objects templater, not
        wfm-coupled: a ``playlist`` composer over a ``clip`` source flattens its
        OWN field names (gain / channel), with no waveform vocabulary."""
        cfg = {
            "project": {"name": "studio", "version": "0.1.0"},
            "module": {
                "playlist": {
                    "kind": "composer",
                    "composes": ["clip"],
                    "source": {
                        "object": "clip",
                        "struct": "clip_t",
                        "type_name": "Clip",
                        "fields": [
                            {
                                "name": "gain",
                                "type": "double",
                                "default": "1.0",
                            },
                            {"name": "channel", "type": "int", "default": "0"},
                        ],
                    },
                    "segment": {
                        "type_name": "Track",
                        "struct": "track_t",
                        "sources": "multi",
                        "flat_sources": True,
                        "fields": [{"name": "dur", "type": "size_t"}],
                    },
                    "oo": {"composer_type_name": "Mix"},
                }
            },
        }
        s = _composer.render_segment_type(cfg, "playlist")
        assert "Track_flat_gain(TrackObject *self" in s
        assert "Track_flat_channel(TrackObject *self" in s
        assert (
            'PyObject_GetAttrString(PyList_GET_ITEM(self->sources, 0), "gain")'
            in s
        )
        assert "Track_flat_dur" not in s  # dur is a Track scalar, not proxied
        pyi = _composer.render_pyi(cfg, "playlist")
        assert "    gain: float" in pyi
        assert "    channel: int" in pyi


def _gen_json_cfg():
    """_cfg() with the generic SSOT-driven JSON (no to_json_fn delegation)."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["json"] = {"enabled": True}
    return cfg


class TestFromJsonClsAlloc:
    """``from_json`` / ``from_file`` allocate via ``cls`` (gh-287 round 3 feature
    5): a Python subclass round-trips through the alternate constructors instead
    of being downcast to the base type. The methods are already ``METH_CLASS``,
    so ``cls`` is the (possibly derived) type — alloc through it."""

    def test_delegated_path_allocs_via_cls(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        # both classmethods take the type from cls and alloc through it
        assert "PyTypeObject *type = (PyTypeObject *)cls;" in s
        assert "(ComposerObject *)type->tp_alloc(type, 0)" in s
        # the old hardcoded base-type alloc is gone from the JSON ctors
        assert "ComposerType.tp_alloc(&ComposerType, 0)" not in s

    def test_generated_path_threads_type_through_wrap(self):
        s = _composer.render_composer_type(_gen_json_cfg(), "wfm_compose")
        # the shared wrapper takes the type; from_json/from_file pass cls
        assert (
            "_Composer_wrap_state(PyTypeObject *type, wfm_compose_state_t *st)"
            in s
        )
        assert "(ComposerObject *)type->tp_alloc(type, 0)" in s
        assert "_Composer_wrap_state((PyTypeObject *)cls, st)" in s
        assert "ComposerType.tp_alloc(&ComposerType, 0)" not in s


def _to_dict_cfg():
    """_cfg() with the composer opting into the generic to_dict() (feature 5)."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["composer"] = {"to_dict": True}
    return cfg


class TestComposerToDict:
    """``[module.X.composer] to_dict`` (gh-287 round 3 feature 5): a generic
    ``Composer.to_dict()`` that serializes the *resolved* composition into a
    plain nested dict (repeat / continuous / segments → sources), driven by the
    SSOT field names and the OO getsets — the generic introspection primitive
    any sidecar (SigMF / BLUE / …) is built from in Python, not by jm."""

    def test_method_and_helper_emitted(self):
        s = _composer.render_composer_type(_to_dict_cfg(), "wfm_compose")
        assert "Composer_to_dict(ComposerObject *self" in s
        # a generic per-object serializer driven by a key array
        assert (
            "_Composer_obj_to_dict(PyObject *o, const char *const *keys)" in s
        )
        assert "PyObject_GetAttrString(o, keys[i])" in s
        # reuses the resolved-spec path (so segments reflect the live state)
        assert "_Composer_resolved(self)" in s
        # method row wired in
        assert '{"to_dict", (PyCFunction)Composer_to_dict, METH_NOARGS,' in s

    def test_key_arrays_from_ssot(self):
        s = _composer.render_composer_type(_to_dict_cfg(), "wfm_compose")
        # segment keys = the segment's OWN fields; source keys = source fields
        assert (
            '_Composer_seg_keys[] = { "fs", "num_samples", "off_samples", NULL };'
            in s
        )
        assert '_Composer_src_keys[] = { "type", "freq",' in s
        assert (
            ', "bits", NULL };' in s
        )  # bytes field included, NULL-terminated

    def test_nested_shape(self):
        s = _composer.render_composer_type(_to_dict_cfg(), "wfm_compose")
        # the dict is {repeat, continuous, segments:[{..., sources:[...]}]}
        assert 'PyDict_SetItemString(sd, "sources", src_out)' in s
        assert '"{s:O,s:O,s:N}", "repeat",' in s
        assert '"continuous",' in s and '"segments", segs_out' in s

    def test_absent_by_default(self):
        s = _composer.render_composer_type(_cfg(), "wfm_compose")
        assert "Composer_to_dict" not in s
        assert "_Composer_obj_to_dict" not in s

    def test_pyi_declares_to_dict(self):
        pyi = _composer.render_pyi(_to_dict_cfg(), "wfm_compose")
        assert "    def to_dict(self) -> dict: ..." in pyi
        assert "def to_dict(" not in _composer.render_pyi(
            _cfg(), "wfm_compose"
        )

    def test_generic_non_wfm_composer(self):
        """Proves to_dict is a generic object-of-objects primitive: a playlist
        composer serializes its OWN field names (dur / gain / channel)."""
        cfg = {
            "project": {"name": "studio", "version": "0.1.0"},
            "module": {
                "playlist": {
                    "kind": "composer",
                    "backing": "playlist",
                    "composes": ["clip"],
                    "composer": {"to_dict": True},
                    "source": {
                        "object": "clip",
                        "struct": "clip_t",
                        "type_name": "Clip",
                        "fields": [
                            {
                                "name": "gain",
                                "type": "double",
                                "default": "1.0",
                            },
                            {"name": "channel", "type": "int", "default": "0"},
                        ],
                    },
                    "segment": {
                        "type_name": "Track",
                        "struct": "track_t",
                        "sources": "multi",
                        "fields": [{"name": "dur", "type": "size_t"}],
                    },
                    "timeline": {"type_name": "Reel", "loop": ["once"]},
                    "oo": {"composer_type_name": "Mix"},
                }
            },
        }
        s = _composer.render_composer_type(cfg, "playlist")
        assert "Mix_to_dict(MixObject *self" in s
        assert '_Mix_seg_keys[] = { "dur", NULL };' in s
        assert '_Mix_src_keys[] = { "gain", "channel", NULL };' in s


class TestRealtimeStream:
    """gh-317 feature 1: `realtime = {clock_create, pace, destroy, header}` paces
    the generated stream() iterator to an fs-Hz clock IN the .so, so a project
    drops its hand-written `paced()` helper. Off by default (plain stream)."""

    def _rt_cfg(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["composer"] = {
            "stream": True,
            "realtime": {
                "clock_create": "dp_sample_clock_create",
                "pace": "dp_sample_clock_pace",
                "destroy": "dp_sample_clock_destroy",
                "header": "timing/timing_core.h",
            },
        }
        return cfg

    def test_iterator_paces_in_c(self):
        s = _composer.render_composer_type(self._rt_cfg(), "wfm_compose")
        # opaque clock on the iterator struct, lazily created, paced by count.
        assert "double realtime;" in s and "void *clk;" in s
        assert "self->clk = dp_sample_clock_create(self->realtime, 0);" in s
        assert "dp_sample_clock_pace(self->clk, (size_t)n);" in s
        assert "dp_sample_clock_destroy(self->clk);" in s  # dealloc
        # stream(block, realtime=) parses both.
        assert 'static char *kwlist[] = {"block", "realtime", NULL};' in s
        assert 'ParseTupleAndKeywords(args, kwds, "|nd"' in s

    def test_realtime_header_included(self):
        ext = _composer.render_ext(self._rt_cfg(), "wfm_compose")
        assert '#include "timing/timing_core.h"' in ext

    def test_plain_stream_unchanged(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["composer"] = {"stream": True}
        s = _composer.render_composer_type(cfg, "wfm_compose")
        # no realtime machinery when the sub-table is absent (back-compat).
        assert "self->realtime" not in s
        assert "->clk" not in s
        assert 'ParseTupleAndKeywords(args, kwds, "|n", kwlist, &block))' in s


class TestDelegatedSerializers:
    """gh-317 feature 2 / gh-313: `[[module.X.serializers]]` generates additional
    delegated serializers (to_sigmf, …) — a `<Composer>.<name>(params) -> str`
    that coerces leading scalar/enum params and calls the project's C serializer
    `fn(<params>, segs, n)` over the resolved segments. The sanctioned path for
    domain wire formats jm generates none of."""

    def _cfg(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["serializers"] = [
            {
                "name": "to_sigmf",
                "fn": "wfm_sigmf_meta_json",
                "returns": "str",
                "params": [
                    {
                        "name": "kind",
                        "type": "int",
                        "enum": "wfm_type",
                        "default": "tone",
                    },
                    {"name": "fs", "type": "double", "default": "1e6"},
                    {"name": "fc", "type": "double", "default": "0.0"},
                ],
            }
        ]
        return cfg

    def test_serializer_method_codegen(self):
        s = _composer.render_composer_type(self._cfg(), "wfm_compose")
        assert (
            "Composer_to_sigmf(ComposerObject *self, "
            "PyObject *args, PyObject *kwds)" in s
        )
        # leading params parse (enum as string, scalars), with defaults.
        assert 'static char *kwlist[] = {"kind", "fs", "fc", NULL};' in s
        assert 'ParseTupleAndKeywords(args, kwds, "|sdd"' in s
        # enum param validates to its SSOT int.
        assert "int _e_kind = _enum_index(_enum_wfm_type, kind);" in s
        # fetch the resolved segments, then delegate: fn(<params>, segs, n).
        assert "wfm_compose_segments(self->state, &_n, &_rep, &_cont);" in s
        assert "wfm_sigmf_meta_json(_e_kind, fs, fc, segs, _n);" in s
        assert "PyUnicode_FromString(_js);" in s
        # registered in the method table.
        assert '{"to_sigmf",' in s
        assert "METH_VARARGS | METH_KEYWORDS" in s

    def test_round_trips_through_toml(self, tmp_path):
        from just_makeit import _config as C

        C.save(tmp_path, self._cfg())
        sers = C.composer_serializers(C.load(tmp_path), "wfm_compose")
        assert sers and sers[0]["name"] == "to_sigmf"
        assert sers[0]["fn"] == "wfm_sigmf_meta_json"
        assert sers[0]["params"][0]["enum"] == "wfm_type"
        assert sers[0]["params"][1]["name"] == "fs"

    def test_pyi_exposes_serializer(self):
        pyi = _composer.render_pyi(self._cfg(), "wfm_compose")
        assert (
            "def to_sigmf(self, kind: str = ..., fs: float = ..., "
            "fc: float = ...) -> str: ..." in pyi
        )

    def test_no_params_serializer_is_noargs(self):
        cfg = _cfg()
        cfg["module"]["wfm_compose"]["serializers"] = [
            {"name": "to_blue", "fn": "wfm_blue_meta"}
        ]
        s = _composer.render_composer_type(cfg, "wfm_compose")
        assert (
            "Composer_to_blue(ComposerObject *self, "
            "PyObject *Py_UNUSED(ignored))" in s
        )
        assert "wfm_blue_meta(segs, _n);" in s
        assert '{"to_blue", (PyCFunction)Composer_to_blue,' in s
