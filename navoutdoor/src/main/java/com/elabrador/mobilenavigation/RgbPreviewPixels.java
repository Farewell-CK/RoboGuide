package com.elabrador.mobilenavigation;

/** Converts a strided RGB8 frame to a small opaque ARGB preview without flipping it. */
final class RgbPreviewPixels {
    private RgbPreviewPixels() {}

    static int[] convert(byte[] rgb, int width, int height, int stride, int step) {
        if (rgb == null || width <= 0 || height <= 0 || step <= 0
                || stride < width * 3L || rgb.length < (height - 1L) * stride + width * 3L)
            throw new IllegalArgumentException("Incomplete RGB8 frame");
        int w=(width+step-1)/step, h=(height+step-1)/step;
        int[] pixels=new int[w*h];
        for(int y=0;y<h;y++) for(int x=0;x<w;x++) {
            int i=y*step*stride+x*step*3;
            pixels[y*w+x]=0xff000000 | ((rgb[i]&255)<<16)
                    | ((rgb[i+1]&255)<<8) | (rgb[i+2]&255);
        }
        return pixels;
    }
}
