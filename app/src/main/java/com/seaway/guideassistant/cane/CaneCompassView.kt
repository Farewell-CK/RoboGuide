package com.seaway.guideassistant.cane

import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import com.seaway.guideassistant.R
import com.seaway.guideassistant.ble.cane.CaneAttitude

/**
 * 指南针（设计稿 88×88）：表盘固定（上北），箭头指向杖尖绝对方位 orientation（磁北 0°、顺时针）。
 * 所有尺寸按控件宽度等比换算。
 */
class CaneCompassView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : View(context, attrs, defStyle) {

    private val cyan = ContextCompat.getColor(context, R.color.cane_state_swing)

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; color = cyan }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        color = ContextCompat.getColor(context, R.color.white)
        typeface = Typeface.DEFAULT_BOLD
    }
    private val arrow = Path()

    private var orientation01deg: Int? = null

    /** null 表示方位不可信（标定中），箭头变暗 */
    fun update(orientation: Int?) {
        orientation01deg = orientation
        contentDescription = context.getString(
            R.string.cane_cd_compass_fmt, orientation?.let { CaneAttitude.degText(it) } ?: "--"
        )
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        setMeasuredDimension(w, w)
    }

    override fun onDraw(canvas: Canvas) {
        val k = width / 88f
        val c = width / 2f

        fillPaint.color = Color_BG
        canvas.drawCircle(c, c, c - 0.7f * k, fillPaint)
        linePaint.strokeWidth = 1.4f * k
        canvas.drawCircle(c, c, c - 0.7f * k, linePaint)
        linePaint.strokeWidth = 1f * k
        linePaint.pathEffect = DashPathEffect(floatArrayOf(2f * k, 8f * k), 0f)
        canvas.drawCircle(c, c, 42f * k - 0.5f * k, linePaint)
        linePaint.pathEffect = null

        textPaint.textSize = 21f * k
        canvas.drawText("N", c, 25f * k, textPaint)

        // 箭头 26×37：顶点朝上，绕表盘中心旋转 orientation
        val o = orientation01deg
        canvas.save()
        canvas.rotate((o ?: 0) / 10f, c, c)
        arrow.reset()
        arrow.moveTo(c, c - 18.5f * k)
        arrow.lineTo(c + 13f * k, c + 18.5f * k)
        arrow.lineTo(c, c + 9f * k)
        arrow.lineTo(c - 13f * k, c + 18.5f * k)
        arrow.close()
        fillPaint.color = cyan
        fillPaint.alpha = if (o != null) 255 else 90
        canvas.drawPath(arrow, fillPaint)
        fillPaint.alpha = 255
        canvas.restore()
    }

    private companion object {
        const val Color_BG = 0xFF001B32.toInt()
    }
}
