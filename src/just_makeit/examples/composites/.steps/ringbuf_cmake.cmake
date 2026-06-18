# native/src/ringbuf/CMakeLists.txt
add_library(ringbuf_core OBJECT ringbuf.c)
target_include_directories(ringbuf_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
