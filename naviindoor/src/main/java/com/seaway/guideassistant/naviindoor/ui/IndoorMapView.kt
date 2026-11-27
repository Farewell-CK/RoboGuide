package com.seaway.guideassistant.naviindoor.ui

import android.content.Context
import android.graphics.*
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.widget.Toast
import com.seaway.guideassistant.naviindoor.core.IndoorPath
import com.seaway.guideassistant.naviindoor.data.MapNode

class IndoorMapView(context: Context): View(context) {
    var image: Bitmap? = null; set(value) { field = value; zoom = 1f; panX = 0f; panY = 0f; invalidate() }
    var nodes = emptyList<MapNode>(); set(value) { field = value; invalidate() }
    var routes = emptyList<IndoorPath>(); set(value) { field = value; invalidate() }
    var currentId: String? = null; set(value) { field = value; invalidate() }
    var pick: ((Double, Double) -> Unit)? = null
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bounds = RectF()
    private var zoom = 1f
    private var panX = 0f
    private var panY = 0f
    private var selectedId: String? = null
    private val scaleGesture = ScaleGestureDetector(context, object: ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            val before = zoom
            zoom = (zoom * detector.scaleFactor).coerceIn(1f, 5f)
            val factor = zoom / before
            panX = detector.focusX - width / 2f - (detector.focusX - width / 2f - panX) * factor
            panY = detector.focusY - height / 2f - (detector.focusY - height / 2f - panY) * factor
            constrainPan(); invalidate(); return true
        }
    })
    private val gesture = GestureDetector(context, object: GestureDetector.SimpleOnGestureListener() {
        override fun onDown(event: MotionEvent) = true
        override fun onScroll(first: MotionEvent?, event: MotionEvent, distanceX: Float, distanceY: Float): Boolean {
            if (!scaleGesture.isInProgress) { panX -= distanceX; panY -= distanceY; constrainPan(); invalidate() }
            return true
        }
        override fun onDoubleTap(event: MotionEvent): Boolean {
            zoom = if (zoom > 1f) 1f else 2.5f; panX = 0f; panY = 0f; invalidate(); return true
        }
        override fun onSingleTapUp(event: MotionEvent): Boolean {
            val bitmap = image ?: return true
            updateBounds()
            if (!bounds.contains(event.x, event.y)) return true
            performClick()
            val x = (event.x - bounds.left) / bounds.width() * bitmap.width
            val y = (event.y - bounds.top) / bounds.height() * bitmap.height
            val picker = pick
            if (picker != null) picker(x.toDouble(), y.toDouble())
            else nodes.minByOrNull { node ->
                val sx = bounds.left + node.x / bitmap.width * bounds.width()
                val sy = bounds.top + node.y / bitmap.height * bounds.height()
                (sx - event.x) * (sx - event.x) + (sy - event.y) * (sy - event.y)
            }?.let { node ->
                val sx = bounds.left + node.x / bitmap.width * bounds.width()
                val sy = bounds.top + node.y / bitmap.height * bounds.height()
                if (kotlin.math.hypot(sx - event.x, sy - event.y) <= 28 * resources.displayMetrics.density) {
                    selectedId = node.id; invalidate(); Toast.makeText(context, node.name, Toast.LENGTH_SHORT).show()
                }
            }
            return true
        }
    })
    private fun updateBounds() {
        val bitmap = image ?: return
        val scale = minOf(width.toFloat() / bitmap.width, height.toFloat() / bitmap.height) * zoom
        val left = (width - bitmap.width * scale) / 2 + panX
        val top = (height - bitmap.height * scale) / 2 + panY
        bounds.set(left, top, left + bitmap.width * scale, top + bitmap.height * scale)
    }
    private fun constrainPan() {
        val bitmap = image ?: return
        val scale = minOf(width.toFloat() / bitmap.width, height.toFloat() / bitmap.height) * zoom
        val limitX = ((bitmap.width * scale - width) / 2).coerceAtLeast(0f)
        val limitY = ((bitmap.height * scale - height) / 2).coerceAtLeast(0f)
        panX = panX.coerceIn(-limitX, limitX); panY = panY.coerceIn(-limitY, limitY)
    }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(Color.WHITE)
        val bitmap = image ?: return
        updateBounds()
        val scale = bounds.width() / bitmap.width
        val left = bounds.left; val top = bounds.top
        paint.style = Paint.Style.FILL; paint.pathEffect = null
        canvas.drawBitmap(bitmap, null, bounds, paint)
        paint.color = Color.rgb(0, 150, 136); paint.style = Paint.Style.STROKE
        paint.strokeWidth = 3 * resources.displayMetrics.density
        paint.pathEffect = DashPathEffect(floatArrayOf(10f, 7f), 0f)
        routes.distinctBy { it.startId to it.endId }.forEach { route ->
            val start = nodes.firstOrNull { it.id == route.startId }
            val end = nodes.firstOrNull { it.id == route.endId }
            if (start != null && end != null) canvas.drawLine(left + (start.x * scale).toFloat(), top + (start.y * scale).toFloat(), left + (end.x * scale).toFloat(), top + (end.y * scale).toFloat(), paint)
        }
        paint.pathEffect = null; paint.style = Paint.Style.FILL
        for (node in nodes) {
            val x = left + (node.x * scale).toFloat(); val y = top + (node.y * scale).toFloat()
            paint.color = if (node.id == currentId) Color.rgb(37, 99, 235) else Color.rgb(234, 88, 12)
            if (node.id == selectedId) { paint.style = Paint.Style.STROKE; paint.strokeWidth = 2 * resources.displayMetrics.density; canvas.drawCircle(x, y, 13 * resources.displayMetrics.density, paint); paint.style = Paint.Style.FILL }
            canvas.drawCircle(x, y, 7 * resources.displayMetrics.density, paint)
            paint.textSize = 13 * resources.displayMetrics.scaledDensity
            paint.color = Color.BLACK
            canvas.drawText(node.name, x + 10, y - 12, paint)
        }
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (image == null) return super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_DOWN) parent?.requestDisallowInterceptTouchEvent(true)
        scaleGesture.onTouchEvent(event); gesture.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) parent?.requestDisallowInterceptTouchEvent(false)
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}
