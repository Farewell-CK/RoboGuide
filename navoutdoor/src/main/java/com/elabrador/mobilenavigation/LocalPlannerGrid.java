package com.elabrador.mobilenavigation;

/** Source-compatible target projection with pedestrian-safe obstacle inflation. */
final class LocalPlannerGrid {
    static final int SAFETY_RADIUS_CELLS = 3;
    static final int SAFETY_COST = 20;

    private LocalPlannerGrid() {}
    static final class Target { final int row,col; Target(int row,int col){this.row=row;this.col=col;} }
    static Target boundaryTarget(int rows,int cols,float locRow,float locCol,float dirRow,float dirCol){
        float len=(float)Math.hypot(dirRow,dirCol); if(len<1e-6f) throw new IllegalArgumentException("Target direction has near-zero length.");
        dirRow/=len; dirCol/=len; float best=Float.POSITIVE_INFINITY;
        if(Math.abs(dirCol)>1e-6f){float t=dirCol>0?((cols-1)-locCol)/dirCol:(0-locCol)/dirCol;if(t>0)best=Math.min(best,t);}
        if(Math.abs(dirRow)>1e-6f){float t=dirRow>0?((rows-1)-locRow)/dirRow:(0-locRow)/dirRow;if(t>0)best=Math.min(best,t);}
        if(!Float.isFinite(best)) throw new IllegalArgumentException("Direction does not reach map boundary.");
        int row=Math.max(0,Math.min(rows-1,pythonRound(locRow+dirRow*best))); int col=Math.max(0,Math.min(cols-1,pythonRound(locCol+dirCol*best))); return new Target(row,col);
    }
    static int[][] preprocess(int[][] source){
        int rows=source.length,cols=source[0].length; int[][] result=new int[rows][cols];
        boolean[][] occupied=new boolean[rows][cols];
        for(int r=0;r<rows;r++)for(int q=0;q<cols;q++)
            occupied[r][q]=source[r][q]>0;

        // Exact source behavior: cv2.dilate(..., RECT(7,7)) followed by assigning
        // cost 20 only to newly occupied cells. This is a traversable warning band,
        // not an extra hard obstacle layer.
        for(int r=0;r<rows;r++)for(int q=0;q<cols;q++){
            boolean nearObstacle=false;
            for(int dr=-SAFETY_RADIUS_CELLS;dr<=SAFETY_RADIUS_CELLS && !nearObstacle;dr++)
                for(int dq=-SAFETY_RADIUS_CELLS;dq<=SAFETY_RADIUS_CELLS;dq++){
                    int rr=r+dr,qq=q+dq;
                    if(rr>=0&&rr<rows&&qq>=0&&qq<cols&&occupied[rr][qq]){
                        nearObstacle=true; break;
                    }
                }
            if(source[r][q]>0) result[r][q]=source[r][q];
            else if(nearObstacle) result[r][q]=SAFETY_COST;
            else result[r][q]=0;
            if(source[r][q]<0) result[r][q]=50;
        }
        return result;
    }

    /** Python 3 round() uses ties-to-even, unlike Java Math.round(). */
    static int pythonRound(float value) { return (int)Math.rint(value); }
}
