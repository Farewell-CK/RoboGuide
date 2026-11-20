package com.elabrador.mobilenavigation;

import java.io.*;
import java.util.Arrays;
import java.util.zip.*;

/** Lossless file delta. Big-endian DataOutput header, gzip container; no voxel semantics inferred. */
final class SnapshotPatch {
    private static final int MAGIC=0x4e445431;
    static void create(File previous,File next,File patch)throws IOException {
        try(RandomAccessFile before=new RandomAccessFile(previous,"r");RandomAccessFile after=new RandomAccessFile(next,"r");
            DataOutputStream out=new DataOutputStream(new GZIPOutputStream(new FileOutputStream(patch)))) {
            out.writeInt(MAGIC);out.writeLong(after.length());byte[] a=new byte[65536],b=new byte[65536];
            for(long offset=0;offset<after.length();offset+=65536) {
                int length=after.read(b);int n=before.read(a,0,length);
                if(n==length&&Arrays.equals(Arrays.copyOf(a,length),Arrays.copyOf(b,length)))continue;
                out.writeLong(offset);out.writeInt(length);out.write(b,0,length);
            }
            out.writeLong(-1);
        }
    }
    static void apply(File previous,File patch,File destination)throws IOException {
        try(InputStream in=new FileInputStream(previous);OutputStream out=new FileOutputStream(destination)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)out.write(b,0,n);}
        try(DataInputStream in=new DataInputStream(new GZIPInputStream(new FileInputStream(patch)));RandomAccessFile out=new RandomAccessFile(destination,"rw")) {
            if(in.readInt()!=MAGIC)throw new IOException("Bad snapshot patch");long size=in.readLong();if(size<0)throw new IOException("Bad snapshot size");out.setLength(size);
            byte[] b=new byte[65536];long offset;while((offset=in.readLong())!=-1){int n=in.readInt();if(offset<0||n<0||n>b.length||offset+n>size)throw new IOException("Bad patch span");in.readFully(b,0,n);out.seek(offset);out.write(b,0,n);}out.getFD().sync();
        }
    }
}
