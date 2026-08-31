package com.elabrador.mobilenavigation;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.util.AttributeSet;
import android.view.View;

/** Android renderer for local_planner.py's map_data_visualize matrix. */
public final class LocalPlanView extends View {
    private final Paint cellPaint = new Paint();
    private final Paint emptyPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint personPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint personOutlinePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private LocalPlanner.PathResult result = LocalPlanner.PathResult.waitingForTarget();

    public LocalPlanView(Context context, AttributeSet attrs) {
        super(context, attrs);
        emptyPaint.setColor(Color.rgb(94, 107, 120));
        emptyPaint.setTextAlign(Paint.Align.CENTER);
        emptyPaint.setTextSize(14f * getResources().getDisplayMetrics().scaledDensity);
        personPaint.setColor(Color.rgb(33, 150, 243));
        personOutlinePaint.setColor(Color.WHITE);
        personOutlinePaint.setStyle(Paint.Style.STROKE);
        personOutlinePaint.setStrokeWidth(2f * getResources().getDisplayMetrics().density);
    }

    void setPlan(LocalPlanner.PathResult result) {
        this.result = result == null ? LocalPlanner.PathResult.waitingForTarget() : result;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int[][] grid = result.visualizationGrid;
        if (grid == null || grid.length == 0 || grid[0].length == 0) {
            canvas.drawColor(Color.rgb(238, 241, 244));
            String message = result.waitingReason == null
                    ? "等待局部代价图和 A* 路径" : result.waitingReason;
            canvas.drawText(message, getWidth() / 2f,
                    getHeight() / 2f - (emptyPaint.ascent() + emptyPaint.descent()) / 2f,
                    emptyPaint);
            return;
        }

        int rows = grid.length, cols = grid[0].length;
        float cell = Math.min(getWidth() / (float) cols, getHeight() / (float) rows);
        float left = (getWidth() - cols * cell) / 2f;
        float top = (getHeight() - rows * cell) / 2f;
        for (int row = 0; row < rows; row++) {
            for (int col = 0; col < cols; col++) {
                cellPaint.setColor(costColor(grid[row][col]));
                float x = left + col * cell;
                float y = top + displayRow(rows, row) * cell;
                canvas.drawRect(x, y, x + cell + 0.5f, y + cell + 0.5f, cellPaint);
            }
        }
        drawPersonMarker(canvas, left + cols * cell / 2f, top + rows * cell / 2f, cell);
    }

    private void drawPersonMarker(Canvas canvas, float centerX, float centerY, float cell) {
        float density = getResources().getDisplayMetrics().density;
        float radius = Math.max(cell * 1.8f, 7f * density);
        canvas.drawCircle(centerX, centerY, radius, personPaint);
        canvas.drawCircle(centerX, centerY, radius, personOutlinePaint);

        Path direction = new Path();
        direction.moveTo(centerX, centerY - radius * 1.9f);
        direction.lineTo(centerX - radius * 0.62f, centerY - radius * 0.15f);
        direction.lineTo(centerX + radius * 0.62f, centerY - radius * 0.15f);
        direction.close();
        canvas.drawPath(direction, personPaint);
        canvas.drawPath(direction, personOutlinePaint);
    }

    static int displayRow(int rows, int mapRow) {
        return rows - 1 - mapRow;
    }

    static int costColor(int value) {
        if (value == 127) return Color.rgb(0, 188, 212);
        if (value < 0) return Color.rgb(224, 228, 232);

        int cost = Math.max(0, Math.min(100, value));
        if (cost <= 20) {
            return interpolateColor(216, 242, 222, 118, 190, 130, cost / 20f);
        }
        if (cost <= 60) {
            return interpolateColor(118, 190, 130, 250, 196, 76, (cost - 20) / 40f);
        }
        if (cost <= 90) {
            return interpolateColor(250, 196, 76, 238, 124, 50, (cost - 60) / 30f);
        }
        return interpolateColor(238, 124, 50, 179, 38, 30, (cost - 90) / 10f);
    }

    private static int interpolateColor(int redStart, int greenStart, int blueStart,
                                        int redEnd, int greenEnd, int blueEnd, float ratio) {
        float clamped = Math.max(0f, Math.min(1f, ratio));
        int red = Math.round(redStart + (redEnd - redStart) * clamped);
        int green = Math.round(greenStart + (greenEnd - greenStart) * clamped);
        int blue = Math.round(blueStart + (blueEnd - blueStart) * clamped);
        return Color.rgb(red, green, blue);
    }
}
