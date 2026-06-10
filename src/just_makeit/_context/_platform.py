"""_platform — Windows CMake boilerplate, gated by ``[project] platforms``.

jm emits CPython extensions for MinGW/GCC; on Windows that needs a little
runtime-DLL plumbing in the generated ``CMakeLists.txt`` (``-static-libgcc`` and
copying ``libwinpthread-1.dll`` next to the ``.pyd``). For a project that does
not target Windows this is untested boilerplate frozen in place by the drift
gate (gh-213), so it is now **opt-in**: emitted only when the manifest lists
``windows`` in ``[project] platforms`` (off by default).

The slots default to ``""`` so a non-Windows project renders the
``CMakeLists.txt`` without any ``if(WIN32 …)`` block, and ``jm status --check``
treats that absence as correct. Names are resolved here (no nested
placeholders) so ``_render.render`` stays single-pass.
"""

from __future__ import annotations

_EMPTY = {
    "win_cmake_component": "",
    "win_cmake_module": "",
}


def make_platform_ctx(
    windows: bool, *, component: str = "", module: str = ""
) -> dict[str, str]:
    """Per-target Windows CMake render slots, filled only when *windows*.

    Covers the per-component / per-module ``CMakeLists.txt`` blocks (the
    boilerplate gh-213 froze across every component). The single
    configure-time ``libwinpthread`` copy in the top ``CMakeLists.txt`` is a
    harmless no-op off Windows and is left always-present (it is maintained by
    apply's sentinel splicing, not a full re-render).

    ``component`` / ``module`` are the CMake target names spliced into the
    blocks; pass whichever applies to the template being rendered.
    """
    if not windows:
        return dict(_EMPTY)

    out = dict(_EMPTY)
    if component:
        out["win_cmake_component"] = (
            'if(WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")\n'
            "    # Avoid pulling in libgcc_s_seh-1.dll at runtime;"
            " libwinpthread-1.dll\n"
            "    # is copied once at configure time by the top CMakeLists.\n"
            f"    target_link_options({component} PRIVATE -static-libgcc)\n"
            "endif()\n"
        )
    if module:
        out["win_cmake_module"] = (
            'if(WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")\n'
            f"    target_link_options({module} PRIVATE -static-libgcc)\n"
            '    get_filename_component(gcc_bin "${CMAKE_C_COMPILER}"'
            " DIRECTORY)\n"
            "    foreach(dll_name IN ITEMS libwinpthread-1.dll)\n"
            '        if(EXISTS "${gcc_bin}/${dll_name}")\n'
            f"            add_custom_command(TARGET {module} POST_BUILD\n"
            "                COMMAND ${CMAKE_COMMAND} -E copy_if_different\n"
            '                    "${gcc_bin}/${dll_name}"\n'
            f'                    "${{PYTHON_PACKAGE_DIR}}/{module}"\n'
            "                VERBATIM\n"
            '                COMMENT "Copy Windows runtime DLL ${dll_name}")\n'
            "        endif()\n"
            "    endforeach()\n"
            "endif()\n"
        )
    return out
