package com.elabrador.mobilenavigation;
import org.junit.Test;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.util.Random;

public class ArchiveFormatTest {
    @Test public void snapshotDeltasRestoreChangedGrownAndTruncatedFiles() throws Exception {
        File dir=Files.createTempDirectory("map-delta").toFile(),base=new File(dir,"base"),next=new File(dir,"next"),patch=new File(dir,"delta.gz"),restored=new File(dir,"out");
        byte[] initial=new byte[200000];new Random(12).nextBytes(initial);Files.write(base.toPath(),initial);
        for(int size:new int[]{240001,199999,80000,0}) {
            byte[] target=java.util.Arrays.copyOf(initial,size);if(size>10)target[10]=99;Files.write(next.toPath(),target);
            SnapshotPatch.create(base,next,patch);SnapshotPatch.apply(base,patch,restored);
            assertArrayEquals(target,Files.readAllBytes(restored.toPath()));
        }
    }
    @Test public void completeMapBundlesAreDeterministicAndKeepSemanticPayload() throws Exception {
        File dir=Files.createTempDirectory("map-bundle").toFile(),ot=new File(dir,"map.ot"),sem=new File(dir,"sem.bin"),one=new File(dir,"one.navmap"),two=new File(dir,"two.navmap");
        byte[] occupancy={1,2,3},semantic={8,7,6,5};Files.write(ot.toPath(),occupancy);Files.write(sem.toPath(),semantic);
        MapBundle.write(ot,sem,one);MapBundle.write(ot,sem,two);
        assertArrayEquals(Files.readAllBytes(one.toPath()),Files.readAllBytes(two.toPath()));
        try(java.util.zip.ZipFile zip=new java.util.zip.ZipFile(one)) {
            assertEquals(2,zip.size());assertEquals(4,zip.getEntry("semantics.bin").getSize());
            try(InputStream in=zip.getInputStream(zip.getEntry("semantics.bin"))){byte[] read=new byte[4];assertEquals(4,in.read(read));assertArrayEquals(semantic,read);}
        }
    }

    @Test public void labelsAndConfidenceKeepTheirNumericMeaning() {
        assertArrayEquals(new byte[]{0,13,64,(byte)255},ArchiveBinary.labels(new int[]{0,13,64,255}));
        ByteBuffer floats=ByteBuffer.wrap(ArchiveBinary.floats(new float[]{0.25f,1f})).order(ByteOrder.LITTLE_ENDIAN);
        assertEquals(.25f,floats.getFloat(),0);assertEquals(1f,floats.getFloat(),0);
    }
    @Test public void localDeltaDistinguishesScopeExitsFromUpserts() {
        ArchiveBinary.LocalDelta d=new ArchiveBinary.LocalDelta();
        float[] a={1,2,3,.9f,255,0,0,1};
        assertEquals(1,d.update(a).upserts);assertEquals(0,d.update(a).upserts);
        ArchiveBinary.LocalDelta.Change c=d.update(new float[0]);assertEquals(0,c.upserts);assertEquals(1,c.exits);
        ByteBuffer bytes=ByteBuffer.wrap(c.bytes).order(ByteOrder.LITTLE_ENDIAN);assertEquals(0,bytes.getInt());assertEquals(1,bytes.getInt());assertEquals(1,bytes.getFloat(),0);
    }
}
