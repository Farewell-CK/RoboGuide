package com.elabrador.mobilenavigation;

import java.io.*;
import java.nio.charset.StandardCharsets;
import org.junit.Test;
import static org.junit.Assert.*;

/** Frozen oracle from upstream map_draw.py and the planner's exact OpenCV block. */
public class SourceFilterReferenceTest {
    private static final int SIDE=80, CELLS=SIDE*SIDE;

    private static String text(DataInputStream input) throws IOException {
        int size=input.readInt();assertTrue(size>=0 && size<=4096);
        byte[] bytes=new byte[size];input.readFully(bytes);
        return new String(bytes,StandardCharsets.UTF_8);
    }

    private static int[] ints(byte[] bytes) {
        int[] values=new int[bytes.length];
        for(int i=0;i<values.length;i++)values[i]=bytes[i];
        return values;
    }

    // Keep the oracle independent of whether height masks use byte[] or int[].
    private static int[] ints(Object values) {
        if(values instanceof int[])return (int[])values;
        if(values instanceof byte[])return ints((byte[])values);
        throw new AssertionError("Expected int[] or byte[] filter output");
    }

    private static int[][] matrix(byte[] bytes) {
        int[][] result=new int[SIDE][SIDE];
        for(int i=0;i<CELLS;i++)result[i/SIDE][i%SIDE]=bytes[i];
        return result;
    }

    private void verify(int filter) throws Exception {
        InputStream resource=getClass().getResourceAsStream("/source-filter-reference.bin");
        assertNotNull("Run tools/generate_source_filter_reference.py to create the source oracle",resource);
        try(DataInputStream input=new DataInputStream(new BufferedInputStream(resource))) {
            assertEquals(0x53464631,input.readInt());
            String commit=text(input),drawHash=text(input),plannerHash=text(input),versions=text(input);
            assertEquals("c758acdca0e9d05bb6f52337daefdce9b2b9983e",commit);
            assertEquals(64,drawHash.length());assertEquals(64,plannerHash.length());
            assertTrue(versions.contains("opencv="));
            int cases=input.readInt();assertTrue("Need edge, threshold and random source cases",cases>=70);
            for(int c=0;c<cases;c++) {
                String name=text(input);
                byte[][] data=new byte[5][CELLS];
                for(byte[] values:data)input.readFully(values);
                int[] actual;
                if(filter==0) {
                    actual=ints((Object)MapTransform.sourceMedian(ints(data[0])));
                } else if(filter==1) {
                    actual=ints((Object)MapTransform.sourceHeightEdges(data[1].clone()));
                } else {
                    int[][] planned=LocalPlannerGrid.preprocessSource(matrix(data[0]));
                    assertEquals(name+" planner height",SIDE,planned.length);
                    actual=new int[CELLS];
                    for(int row=0;row<SIDE;row++) {
                        assertEquals(name+" planner width row="+row,SIDE,planned[row].length);
                        System.arraycopy(planned[row],0,actual,row*SIDE,SIDE);
                    }
                }
                assertEquals(name+" output shape",CELLS,actual.length);
                byte[] expected=data[filter+2];
                for(int i=0;i<CELLS;i++)assertEquals(name+" filter="+filter+" row="+(i/SIDE)+" col="+(i%SIDE),
                        (int)expected[i],actual[i]);
            }
            assertEquals("No trailing fixture data",-1,input.read());
            System.out.println("SOURCE_FILTER_REFERENCE filter="+filter+" commit="+commit+
                    " cases="+cases+" compared_cells="+(cases*CELLS)+" "+versions);
        }
    }

    @Test public void sourceMedianMatchesUint8SortingAndReplicatedBorders() throws Exception {verify(0);}
    @Test public void sourceHeightEdgesMatchSobelReflectedBordersAndInclusiveThresholds() throws Exception {verify(1);}
    @Test public void sourcePlanningMatchesGrayscaleDilationAndUnknownRestoration() throws Exception {verify(2);}
}
