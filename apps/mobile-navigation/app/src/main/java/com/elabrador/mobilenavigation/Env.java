package com.elabrador.mobilenavigation;

import java.util.HashSet;
import java.util.Set;

/** Direct port of local_planning.env.Env. */
final class Env {
    static final int HARD_OBSTACLE_COST = 90;
    int xRange = 51, yRange = 31;
    final int[][] motions = {{-1,0},{-1,1},{0,1},{1,1},{1,0},{1,-1},{0,-1},{-1,-1}};
    Set<Long> obstacles = new HashSet<>();
    int[][] map = new int[xRange][yRange];
    void obsMapSet(int rows, int cols, int[][] values) {
        xRange=rows; yRange=cols; map=values; obstacles.clear();
        for(int i=0;i<xRange;i++) for(int j=0;j<yRange;j++) if(isObstacleCost(values[i][j])) obstacles.add(AStar.key(i,j));
    }

    static boolean isObstacleCost(int value) {
        return value > HARD_OBSTACLE_COST && value < 255;
    }
}
