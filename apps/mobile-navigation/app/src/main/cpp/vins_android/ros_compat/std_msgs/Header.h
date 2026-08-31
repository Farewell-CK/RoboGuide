#pragma once

#include <cstdint>
#include <string>

namespace std_msgs {

struct HeaderStamp {
    double seconds = 0.0;
    double toSec() const { return seconds; }
};

struct Header {
    std::uint32_t seq = 0;
    HeaderStamp stamp;
    std::string frame_id;
};

}  // namespace std_msgs

