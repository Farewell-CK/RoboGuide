package com.elabrador.mobilenavigation;

import java.io.*;
import java.util.zip.*;

/** Deterministic complete-map container. .ot is compatible geometry; sidecar is authoritative semantics. */
final class MapBundle {
    static void write(File occupancy,File semantics,File destination)throws IOException {
        try(FileOutputStream file=new FileOutputStream(destination);ZipOutputStream zip=new ZipOutputStream(file)) {
            entry(zip,occupancy,"occupancy.ot");entry(zip,semantics,"semantics.bin");
            zip.finish();zip.flush();file.getFD().sync();
        }
    }
    private static void entry(ZipOutputStream zip,File file,String name)throws IOException {
        CRC32 crc=new CRC32();byte[] buffer=new byte[65536];int n;
        try(InputStream in=new FileInputStream(file)){while((n=in.read(buffer))!=-1)crc.update(buffer,0,n);}
        ZipEntry entry=new ZipEntry(name);entry.setTime(0);entry.setMethod(ZipEntry.STORED);entry.setSize(file.length());entry.setCompressedSize(file.length());entry.setCrc(crc.getValue());zip.putNextEntry(entry);
        try(InputStream in=new FileInputStream(file)){while((n=in.read(buffer))!=-1)zip.write(buffer,0,n);}zip.closeEntry();
    }
}
