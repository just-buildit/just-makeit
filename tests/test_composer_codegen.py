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
