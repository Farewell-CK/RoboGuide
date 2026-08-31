#pragma once

#include <alloca.h>
#include <cstring>

#ifndef strdupa
#define strdupa(value) __extension__ ({ \
    const char* vins_strdupa_source = (value); \
    const std::size_t vins_strdupa_size = std::strlen(vins_strdupa_source) + 1; \
    char* vins_strdupa_copy = static_cast<char*>(alloca(vins_strdupa_size)); \
    std::memcpy(vins_strdupa_copy, vins_strdupa_source, vins_strdupa_size); \
    vins_strdupa_copy; \
})
#endif
