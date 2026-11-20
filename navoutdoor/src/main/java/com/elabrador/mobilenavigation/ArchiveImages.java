package com.elabrador.mobilenavigation;

import android.graphics.Bitmap;
import java.io.ByteArrayOutputStream;

final class ArchiveImages {
    static byte[] jpeg(byte[] rgb,int width,int height,int stride) {
        int[] pixels=RgbPreviewPixels.convert(rgb,width,height,stride,1);
        Bitmap image=Bitmap.createBitmap(pixels,width,height,Bitmap.Config.ARGB_8888);
        try(ByteArrayOutputStream out=new ByteArrayOutputStream()) {
            if(!image.compress(Bitmap.CompressFormat.JPEG,80,out))throw new IllegalStateException("JPEG encoding failed");
            return out.toByteArray();
        }catch(java.io.IOException error){throw new IllegalStateException(error);}
        finally {image.recycle();}
    }
}
