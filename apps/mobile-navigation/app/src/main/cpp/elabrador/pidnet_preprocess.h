#pragma once

namespace elabrador {

// Preserve the reference float operations, channel order and nearest-neighbor
// coordinates. RGB has only 256 possible values per channel.
inline void preparePidNet(const unsigned char* source, int stride, int xOffset,
                         int yOffset, int cropWidth, int cropHeight,
                         int width, int height, float* output) {
    const float mean[3] = {0.485f, 0.456f, 0.406f};
    const float stddev[3] = {0.229f, 0.224f, 0.225f};
    float normalized[3][256];
    for (int channel = 0; channel < 3; ++channel) {
        for (int value = 0; value < 256; ++value) {
            normalized[channel][value] = (value / 255.0f - mean[channel]) / stddev[channel];
        }
    }
    const long long plane = static_cast<long long>(width) * height;
    int previousY = -1;
    for (int y = 0; y < height; ++y) {
        const int sourceY = yOffset + static_cast<long long>(y) * cropHeight / height;
        const long long row = static_cast<long long>(y) * width;
        // Upscaling repeats source rows. Reuse the already normalized row.
        if (sourceY == previousY) {
            for (int channel = 0; channel < 3; ++channel) {
                float* destination = output + channel * plane + row;
                for (int x = 0; x < width; ++x) destination[x] = destination[x - width];
            }
        } else {
            for (int x = 0; x < width; ++x) {
                const int sourceX = xOffset + static_cast<long long>(x) * cropWidth / width;
                const long long index = static_cast<long long>(sourceY) * stride
                        + static_cast<long long>(sourceX) * 3;
                for (int channel = 0; channel < 3; ++channel) {
                    output[channel * plane + row + x] = normalized[channel][source[index + channel]];
                }
            }
        }
        previousY = sourceY;
    }
}

}  // namespace elabrador
