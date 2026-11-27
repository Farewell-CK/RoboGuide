package com.seaway.guideassistant.cane

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import com.seaway.guideassistant.R

/** 碰撞强度：4 个圆点，点亮个数 = 碰撞等级 0..4（设计稿：亮 #00DBFA，灭 #68727A）。 */
class CanePeakDotsView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : View(context, attrs, defStyle) {

    private val onColor = ContextCompat.getColor(context, R.color.cane_cyan)
    private val offColor = ContextCompat.getColor(context, R.color.cane_dot_off)
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private var level = 0

    fun update(level: Int, description: CharSequence? = null) {
        this.level = level.coerceIn(0, DOTS)
        contentDescription = description
        invalidate()
    }

    // 设计稿以 204 宽的列为基准：圆点直径 28、间距 10.5
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        setMeasuredDimension(w, (w * 28f / 204f).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val k = width / 204f
        val d = 28f * k
        val gap = 10.5f * k
        val total = DOTS * d + (DOTS - 1) * gap
        var x = (width - total) / 2f + d / 2f
        for (i in 0 until DOTS) {
            paint.color = if (i < level) onColor else offColor
            canvas.drawCircle(x, height / 2f, d / 2f, paint)
            x += d + gap
        }
    }

    private companion object {
        const val DOTS = 4
    }
}
