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
        Map<Long,Double> g=new HashMap<>();Map<Long,long[]> parent=new HashMap<>();
        PriorityQueue<Node> open=new PriorityQueue<>(Comparator
                .comparingDouble((Node n)->n.f)
                .thenComparingInt(n->x(n.key))
                .thenComparingInt(n->y(n.key)));
        long st=key(start[0],start[1]),go=key(goal[0],goal[1]);
        g.put(st,0.0);g.put(go,Double.POSITIVE_INFINITY);parent.put(st,new long[]{st});
        open.add(new Node(st,0.0,heuristic(start[0],start[1])));
        while(!open.isEmpty()){
            Node n=open.poll();
            double best=g.getOrDefault(n.key,Double.POSITIVE_INFINITY);
            // A cheaper route can enqueue the same cell again. Ignore the older
            // queue entry instead of expanding that cell repeatedly.
            if(Double.compare(n.g,best)!=0)continue;
            int x=x(n.key),y=y(n.key);
            if(n.key==go)break;
            for(int[] m:motions){
                int nx=x+m[0],ny=y+m[1];
                if(collision(x,y,nx,ny))continue;
                long k=key(nx,ny);double ng=best+cost(x,y,nx,ny);
                if(ng<g.getOrDefault(k,Double.POSITIVE_INFINITY)){
                    g.put(k,ng);parent.put(k,new long[]{n.key});
                    open.add(new Node(k,ng,ng+heuristic(nx,ny)));
                }
            }
        }
        if(!parent.containsKey(go))return Collections.emptyList();List<int[]> path=new ArrayList<>();long cur=go;while(true){path.add(new int[]{x(cur),y(cur)});if(cur==st)break;cur=parent.get(cur)[0];}return path;
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
    private boolean collision(int x,int y,int nx,int ny){if(nx<0||ny<0||nx>=rows||ny>=cols||obstacles.contains(key(x,y))||obstacles.contains(key(nx,ny)))return true;if(nx!=x&&ny!=y){if(obstacles.contains(key(nx,y))||obstacles.contains(key(x,ny)))return true;}return false;}
    private double heuristic(int x,int y){return heuristic==Heuristic.MANHATTAN?Math.abs(goal[0]-x)+Math.abs(goal[1]-y):Math.hypot(goal[0]-x,goal[1]-y);}
    static long key(int x,int y){return ((long)x<<32)^(y&0xffffffffL);} private static int x(long k){return (int)(k>>32);}private static int y(long k){return (int)k;}
    private static final class Node{final long key;final double g,f;Node(long k,double g,double f){key=k;this.g=g;this.f=f;}}
}
