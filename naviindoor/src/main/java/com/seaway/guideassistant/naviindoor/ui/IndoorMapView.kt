package com.seaway.guideassistant.naviindoor.ui

import android.content.Context
import android.graphics.*
import android.view.MotionEvent
import android.view.View
import com.seaway.guideassistant.naviindoor.data.MapNode

class IndoorMapView(context: Context): View(context) {
    var image: Bitmap? = null; set(value) { field = value; invalidate() }
    var nodes = emptyList<MapNode>(); set(value) { field = value; invalidate() }
    var currentId: String? = null; set(value) { field = value; invalidate() }
    var pick: ((Double, Double) -> Unit)? = null
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bounds = RectF()
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(Color.rgb(241, 245, 249))
        val bitmap = image ?: return
        val scale = minOf(width.toFloat() / bitmap.width, height.toFloat() / bitmap.height)
        val left = (width - bitmap.width * scale) / 2
        val top = (height - bitmap.height * scale) / 2
        bounds.set(left, top, left + bitmap.width * scale, top + bitmap.height * scale)
        canvas.drawBitmap(bitmap, null, bounds, paint)
        for (node in nodes) {
            val x = left + (node.x * scale).toFloat(); val y = top + (node.y * scale).toFloat()
            paint.color = if (node.id == currentId) Color.rgb(37, 99, 235) else Color.rgb(234, 88, 12)
            canvas.drawCircle(x, y, 7 * resources.displayMetrics.density, paint)
            paint.textSize = 13 * resources.displayMetrics.scaledDensity
            paint.color = Color.BLACK
            canvas.drawText(node.name, x + 10, y - 12, paint)
        }
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (pick == null) return super.onTouchEvent(event)
        if (event.action == MotionEvent.ACTION_UP && bounds.contains(event.x, event.y)) {
            val bitmap = image ?: return false
            performClick()
            pick?.invoke(((event.x - bounds.left) / bounds.width() * bitmap.width).toDouble(),
                ((event.y - bounds.top) / bounds.height() * bitmap.height).toDouble())
        }
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}
