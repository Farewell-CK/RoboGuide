package com.elabrador.mobilenavigation;

import java.util.*;

/** Direct port of nvi_planning/src/local_planning/Astar.py. */
final class AStar {
    private static final double STRAIGHTNESS_TIE_BREAK = 0.01;
    enum Heuristic { MANHATTAN, EUCLIDEAN }
    private final int[] start, goal; private final int[][] map; private final Set<Long> obstacles;
    private final int rows, cols; private final double alpha, turnPenalty; private final Heuristic heuristic;
    private double minimumCellCost;
    private final int[][] motions={{-1,0},{-1,1},{0,1},{1,1},{1,0},{1,-1},{0,-1},{-1,-1}};
    AStar(int[] start,int[] goal,int[][] map,Set<Long> obstacles,Heuristic heuristic,double alpha){this(start,goal,map,obstacles,heuristic,alpha,0.0);}
    AStar(int[] start,int[] goal,int[][] map,Set<Long> obstacles,Heuristic heuristic,double alpha,double turnPenalty){this.start=start;this.goal=goal;this.map=map;this.obstacles=obstacles;this.heuristic=heuristic;this.alpha=alpha;this.turnPenalty=Math.max(0.0,turnPenalty);rows=map.length;cols=map[0].length;}
    List<int[]> searching(){
        if(start[0]<0||start[0]>=rows||start[1]<0||start[1]>=cols
                ||goal[0]<0||goal[0]>=rows||goal[1]<0||goal[1]>=cols
                ||obstacles.contains(key(start[0],start[1]))||obstacles.contains(key(goal[0],goal[1])))
            return Collections.emptyList();
        minimumCellCost=Double.POSITIVE_INFINITY;
        for(int r=0;r<rows;r++)for(int c=0;c<cols;c++)
            if(!obstacles.contains(key(r,c)))minimumCellCost=Math.min(minimumCellCost,Math.abs(map[r][c]));
        if(!Double.isFinite(minimumCellCost))return Collections.emptyList();
        if(turnPenalty>0.0)return searchingWithTurnPenalty();
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
    /** Direction is part of the state, so every real heading change costs once.
     * The penalty deliberately does not depend on turn angle.
     */
    private List<int[]> searchingWithTurnPenalty(){
        final int headings=9,none=8,cells=rows*cols,states=cells*headings;
        double[] g=new double[states];Arrays.fill(g,Double.POSITIVE_INFINITY);
        int[] parent=new int[states];Arrays.fill(parent,-1);
        boolean[] occupied=new boolean[cells];
        for(long obstacle:obstacles){int ox=x(obstacle),oy=y(obstacle);if(ox>=0&&ox<rows&&oy>=0&&oy<cols)occupied[ox*cols+oy]=true;}
        PriorityQueue<DirectionNode> open=new PriorityQueue<>(Comparator
                .comparingDouble((DirectionNode n)->n.f)
                .thenComparingInt(n->n.state/headings/cols)
                .thenComparingInt(n->n.state/headings%cols)
                .thenComparingInt(n->n.state%headings));
        int startCell=start[0]*cols+start[1],goalCell=goal[0]*cols+goal[1];
        int startState=startCell*headings+none;
        g[startState]=0.0;parent[startState]=startState;
        open.add(new DirectionNode(startState,0.0,heuristic(start[0],start[1])));
        int goalState=-1;
        while(!open.isEmpty()){
            DirectionNode n=open.poll();
            if(Double.compare(n.g,g[n.state])!=0)continue;
            int cell=n.state/headings,priorDirection=n.state%headings;
            int row=cell/cols,col=cell%cols;
            if(cell==goalCell){goalState=n.state;break;}
            for(int direction=0;direction<motions.length;direction++){
                int[] motion=motions[direction];int nr=row+motion[0],nc=col+motion[1];
                if(collision(occupied,row,col,nr,nc))continue;
                int nextCell=nr*cols+nc,nextState=nextCell*headings+direction;
                double change=priorDirection==none||priorDirection==direction?0.0:turnPenalty;
                double next=n.g+cost(row,col,nr,nc)+change;
                if(next<g[nextState]){
                    g[nextState]=next;parent[nextState]=n.state;
                    open.add(new DirectionNode(nextState,next,next+heuristic(nr,nc)));
                }
            }
        }
        if(goalState<0)return Collections.emptyList();
        List<int[]> path=new ArrayList<>();int current=goalState;
        while(true){int cell=current/headings;path.add(new int[]{cell/cols,cell%cols});if(current==startState)break;current=parent[current];}
        return path;
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
    // Admissible lower bound for eight-connected movement, including unavoidable
    // cell cost. The previous unit-distance bound ignored road cost and expanded
    // almost the whole grid (nine heading states per cell in the turn search).
    private double heuristic(int x,int y){
        int dx=Math.abs(goal[0]-x),dy=Math.abs(goal[1]-y);
        return alpha*(Math.max(dx,dy)+(Math.sqrt(2)-1)*Math.min(dx,dy))
                +minimumCellCost*Math.max(dx,dy);
    }
    static long key(int x,int y){return ((long)x<<32)^(y&0xffffffffL);} private static int x(long k){return (int)(k>>32);}private static int y(long k){return (int)k;}
    private static final class Node{final int n;final double g,f;Node(int n,double g,double f){this.n=n;this.g=g;this.f=f;}}
    private static final class DirectionNode{final int state;final double g,f;DirectionNode(int state,double g,double f){this.state=state;this.g=g;this.f=f;}}
}
