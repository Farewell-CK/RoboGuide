package com.elabrador.mobilenavigation;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Version 1 wire payloads. All numeric payloads are little endian. */
final class ArchiveBinary {
    private ArchiveBinary() {}
    static byte[] floats(float[] values) {
        ByteBuffer out=ByteBuffer.allocate(values.length*4).order(ByteOrder.LITTLE_ENDIAN);
        out.asFloatBuffer().put(values); return out.array();
    }
    static byte[] doubles(double[] values) {
        ByteBuffer out=ByteBuffer.allocate(values.length*8).order(ByteOrder.LITTLE_ENDIAN);
        out.asDoubleBuffer().put(values); return out.array();
    }
    static byte[] ints(int[] values) {
        ByteBuffer out=ByteBuffer.allocate(values.length*4).order(ByteOrder.LITTLE_ENDIAN);
        out.asIntBuffer().put(values); return out.array();
    }
    static byte[] labels(int[] values) {
        byte[] out=new byte[values.length];
        for(int i=0;i<out.length;i++) {
            if(values[i]<0||values[i]>255)throw new IllegalArgumentException("Label outside uint8");
            out[i]=(byte)values[i];
        }
        return out;
    }
    /** xyz float32, semantic RGB uint32, confidence float32. Includes ground. */
    static byte[] cloud(SemanticPointCloud.Data cloud) {
        int count=cloud.confidence.length;
        if(cloud.xyz.length!=count*3||cloud.semanticRgb.length!=count)
            throw new IllegalArgumentException("Point cloud shape mismatch");
        ByteBuffer out=ByteBuffer.allocate(count*20).order(ByteOrder.LITTLE_ENDIAN);
        for(int i=0;i<count;i++)out.putFloat(cloud.xyz[i*3]).putFloat(cloud.xyz[i*3+1])
                .putFloat(cloud.xyz[i*3+2]).putInt(cloud.semanticRgb[i]).putFloat(cloud.confidence[i]);
        return out.array();
    }
    /** A delta of the currently exported local occupied subset, not the native tree. */
    static final class LocalDelta {
        private Map<Key,float[]> previous=new HashMap<>();
        static final class Change {
            final byte[] bytes;
            final int upserts, exits;
            Change(byte[] bytes,int upserts,int exits){this.bytes=bytes;this.upserts=upserts;this.exits=exits;}
        }
        Change update(float[] leaves) {
            if(leaves.length%8!=0)throw new IllegalArgumentException("Leaf records require 8 floats");
            Map<Key,float[]> next=new HashMap<>(); List<float[]> updates=new ArrayList<>(),exits=new ArrayList<>();
            for(int i=0;i<leaves.length;i+=8) {
                if(!Float.isFinite(leaves[i])||!Float.isFinite(leaves[i+1])||!Float.isFinite(leaves[i+2]))continue;
                Key key=new Key(leaves[i],leaves[i+1],leaves[i+2]);
                float[] row=Arrays.copyOfRange(leaves,i,i+8);next.put(key,row);
                if(!Arrays.equals(previous.get(key),row))updates.add(row);
            }
            for(Map.Entry<Key,float[]> e:previous.entrySet())if(!next.containsKey(e.getKey()))exits.add(e.getValue());
            ByteBuffer out=ByteBuffer.allocate(8+updates.size()*32+exits.size()*12).order(ByteOrder.LITTLE_ENDIAN);
            out.putInt(updates.size()).putInt(exits.size());
            for(float[] row:updates)for(float value:row)out.putFloat(value);
            for(float[] row:exits)out.putFloat(row[0]).putFloat(row[1]).putFloat(row[2]);
            previous=next;return new Change(out.array(),updates.size(),exits.size());
        }
        void clear(){previous.clear();}
        private static final class Key {
            final int x,y,z;
            Key(float x,float y,float z){this.x=Float.floatToIntBits(x==0?0:x);this.y=Float.floatToIntBits(y==0?0:y);this.z=Float.floatToIntBits(z==0?0:z);}
            @Override public boolean equals(Object value){if(!(value instanceof Key))return false;Key k=(Key)value;return x==k.x&&y==k.y&&z==k.z;}
            @Override public int hashCode(){return 31*(31*x+y)+z;}
        }
    }
}
