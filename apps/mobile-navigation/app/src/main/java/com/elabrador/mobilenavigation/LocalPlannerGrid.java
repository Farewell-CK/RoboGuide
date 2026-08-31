package com.elabrador.mobilenavigation;

/** Source-compatible target projection with pedestrian-safe obstacle inflation. */
final class LocalPlannerGrid {
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
        int rows=source.length,cols=source[0].length; int[][] c=new int[rows][cols];
        for(int r=0;r<rows;r++)for(int q=0;q<cols;q++)c[r][q]=source[r][q]<0?0:source[r][q];
        int[][] dilated=new int[rows][cols]; int radius=3;
        for(int r=0;r<rows;r++)for(int q=0;q<cols;q++){
            int hardObstacle=0;
            for(int dr=-radius;dr<=radius;dr++)for(int dq=-radius;dq<=radius;dq++){
                int rr=r+dr,qq=q+dq;
                if(rr>=0&&rr<rows&&qq>=0&&qq<cols&&Env.isObstacleCost(c[rr][qq]))
                    hardObstacle=Math.max(hardObstacle,c[rr][qq]);
            }
            if(Env.isObstacleCost(c[r][q]))dilated[r][q]=Math.max(c[r][q],hardObstacle);
            else dilated[r][q]=hardObstacle>0?Math.max(c[r][q],20):c[r][q];
        }
        c=dilated;
        for(int r=0;r<rows;r++)for(int q=0;q<cols;q++)if(source[r][q]<0)c[r][q]=50;
        return c;
    }

    /** Python 3 round() uses ties-to-even, unlike Java Math.round(). */
    static int pythonRound(float value) { return (int)Math.rint(value); }
}
