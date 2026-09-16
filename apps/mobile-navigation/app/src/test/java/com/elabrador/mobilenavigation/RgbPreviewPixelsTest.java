package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class RgbPreviewPixelsTest {
    @Test public void honorsRgbChannelOrderAndPaddedRows() {
        byte[] rgb={(byte)255,0,0,0,(byte)255,0,77,77,0,0,(byte)255,(byte)255,(byte)255,(byte)255};
        assertArrayEquals(new int[]{0xffff0000,0xff00ff00,0xff0000ff,0xffffffff},
                RgbPreviewPixels.convert(rgb,2,2,8,1));
    }
    @Test public void downsamplesWithoutMirroringOrCroppingLastPixel() {
        byte[] rgb=new byte[3*3*3];
        rgb[0]=1; rgb[6]=2; rgb[18]=3; rgb[24]=4;
        assertArrayEquals(new int[]{0xff010000,0xff020000,0xff030000,0xff040000},
                RgbPreviewPixels.convert(rgb,3,3,9,2));
    }
    @Test(expected=IllegalArgumentException.class) public void rejectsTruncatedFrame() {
        RgbPreviewPixels.convert(new byte[5],2,2,6,2);
    }
}
