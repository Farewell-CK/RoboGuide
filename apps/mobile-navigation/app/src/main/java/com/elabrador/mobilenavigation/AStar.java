package com.elabrador.mobilenavigation;

import java.util.*;

/** Direct port of nvi_planning/src/local_planning/Astar.py. */
final class AStar {
    private static final double STRAIGHTNESS_TIE_BREAK = 0.01;
    enum Heuristic { MANHATTAN, EUCLIDEAN }
    private final int[] start, goal; private final int[][] map; private final Set<Long> obstacles;
    private final int rows, cols; private final double alpha; private final Heuristic heuristic;
    private final int[][] motions={{-1,0},{-1,1},{0,1},{1,1},{1,0},{1,-1},{0,-1},{-1,-1}};
    AStar(int[] start,int[] goal,int[][] map,Set<Long> obstacles,Heuristic heuristic,double alpha){this.start=start;this.goal=goal;this.map=map;this.obstacles=obstacles;this.heuristic=heuristic;this.alpha=alpha;rows=map.length;cols=map[0].length;}
    List<int[]> searching(){
        final int cells=rows*cols;
        double[] g=new double[cells]; Arrays.fill(g,Double.POSITIVE_INFINITY);
        int[] parent=new int[cells]; Arrays.fill(parent,-1);
        boolean[] occupied=new boolean[cells];
        for(long obstacle:obstacles){int ox=x(obstacle),oy=y(obstacle);if(ox>=0&&ox<rows&&oy>=0&&oy<cols)occupied[ox*cols+oy]=true;}
        PriorityQueue<Node> open=new PriorityQueue<>(Comparator
                .comparingDouble((Node n)->n.f)
                .thenComparingInt(node->node.n/cols)
                .thenComparingInt(node->node.n%cols));
        int st=start[0]*cols+start[1],go=goal[0]*cols+goal[1];
        g[st]=0.0;parent[st]=st;
        open.add(new Node(st,0.0,heuristic(start[0],start[1])));
        while(!open.isEmpty()){
            Node n=open.poll();
            double best=g[n.n];
            // A cheaper route can enqueue the same cell again. Ignore the older
            // queue entry instead of expanding that cell repeatedly.
            if(Double.compare(n.g,best)!=0)continue;
            int x=n.n/cols,y=n.n%cols;
            if(n.n==go)break;
            for(int[] m:motions){
                int nx=x+m[0],ny=y+m[1];
                if(collision(occupied,x,y,nx,ny))continue;
                int k=nx*cols+ny;double ng=best+cost(x,y,nx,ny);
                if(ng<g[k]){
                    g[k]=ng;parent[k]=n.n;
                    open.add(new Node(k,ng,ng+heuristic(nx,ny)));
                }
            }
        }
        if(parent[go]<0)return Collections.emptyList();List<int[]> path=new ArrayList<>();int cur=go;while(true){path.add(new int[]{cur/cols,cur%cols});if(cur==st)break;cur=parent[cur];}return path;
    }
    private double cost(int x,int y,int nx,int ny){
        return alpha*Math.hypot(nx-x,ny-y)+Math.abs(map[nx][ny])
                + STRAIGHTNESS_TIE_BREAK*targetRayDeviation(nx,ny);
    }
    private double targetRayDeviation(int x,int y){
        double dx=goal[0]-start[0],dy=goal[1]-start[1],length=Math.hypot(dx,dy);
        if(length<1e-9)return 0.0;
        return Math.abs(dy*(x-start[0])-dx*(y-start[1]))/length;
    }
    private boolean collision(boolean[] occupied,int x,int y,int nx,int ny){if(nx<0||ny<0||nx>=rows||ny>=cols||occupied[x*cols+y]||occupied[nx*cols+ny])return true;if(nx!=x&&ny!=y){if(occupied[nx*cols+y]||occupied[x*cols+ny])return true;}return false;}
    private double heuristic(int x,int y){return heuristic==Heuristic.MANHATTAN?Math.abs(goal[0]-x)+Math.abs(goal[1]-y):Math.hypot(goal[0]-x,goal[1]-y);}
    static long key(int x,int y){return ((long)x<<32)^(y&0xffffffffL);} private static int x(long k){return (int)(k>>32);}private static int y(long k){return (int)k;}
    private static final class Node{final int n;final double g,f;Node(int n,double g,double f){this.n=n;this.g=g;this.f=f;}}
}
