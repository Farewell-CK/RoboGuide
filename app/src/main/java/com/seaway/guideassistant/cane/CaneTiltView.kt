package com.seaway.guideassistant.cane

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import com.seaway.guideassistant.R
import com.seaway.guideassistant.ble.cane.CaneAttitude
import kotlin.math.cos
import kotlin.math.sin

/**
 * 人与盲杖的夹角（设计稿 03b_角度示意 242×362，侧视图，人在右、杖向左下）：
 * 杖身与竖直方向夹角 = tilt，0 竖直握持，90° 完全端平。人物图为 [R.drawable.img_cane_person]。
 * 所有尺寸按控件宽度等比换算。
 */
class CaneTiltView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : View(context, attrs, defStyle) {

    private val cyan = ContextCompat.getColor(context, R.color.cane_state_swing)
    private val person = ContextCompat.getDrawable(context, R.drawable.img_cane_person)
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL; color = Color.WHITE }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
    private val arcRect = RectF()

    private var tilt01deg = 0

    fun update(tilt: Int) {
        tilt01deg = tilt
        contentDescription = context.getString(R.string.cane_cd_tilt_fmt, CaneAttitude.degText(tilt01deg.coerceIn(0, 900)))
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        setMeasuredDimension(w, (w * DESIGN_H / DESIGN_W).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val k = width / DESIGN_W
        val hx = HOLD_X * k
        val hy = HOLD_Y * k

        // 人物：x175 y0，67×217，手臂左端正对握杖点
        person?.setBounds((175f * k).toInt(), 0, (242f * k).toInt(), (217f * k).toInt())
        person?.draw(canvas)

        // 90° 水平参考线（发光）与 0° 竖直参考线
        linePaint.color = (cyan and 0x00FFFFFF) or 0x55000000
        linePaint.strokeWidth = 8f * k
        canvas.drawLine(12f * k, hy, hx, hy, linePaint)
        linePaint.color = cyan
        linePaint.strokeWidth = 3f * k
        canvas.drawLine(12f * k, hy, hx, hy, linePaint)
        canvas.drawLine(hx, hy, hx, 321f * k, linePaint)

        // 夹角弧：从竖直向下(90°)起，向杖身方向(左)顺时针扫过 tilt
        val tiltDeg = CaneAttitude.tiltDegrees(tilt01deg.coerceIn(0, 900))
        val t = Math.toRadians(tiltDeg.toDouble())
        val arcR = 81f * k
        arcRect.set(hx - arcR, hy - arcR, hx + arcR, hy + arcR)
        linePaint.strokeWidth = 2f * k
        canvas.drawArc(arcRect, 90f, tiltDeg, false, linePaint)

        // 盲杖：白色，向左下
        val len = CANE_LEN * k
        val ex = hx - (len * sin(t)).toFloat()
        val ey = hy + (len * cos(t)).toFloat()
        linePaint.color = Color.WHITE
        linePaint.strokeWidth = 4f * k
        canvas.drawLine(hx, hy, ex, ey, linePaint)
        canvas.drawCircle(ex, ey, 5f * k, fillPaint)
        canvas.drawCircle(hx, hy, 6f * k, fillPaint)

        // 刻度文字
        textPaint.typeface = Typeface.DEFAULT
        textPaint.textSize = 26f * k
        textPaint.textAlign = Paint.Align.LEFT
        canvas.drawText("900", 0f, 88f * k, textPaint)
        textPaint.textAlign = Paint.Align.CENTER
        canvas.drawText("0", hx, 356f * k, textPaint)

        // 角度读数：沿弧的角平分线外侧
        val mid = t / 2
        val rr = arcR * 1.28f
        textPaint.textSize = 24f * k
        canvas.drawText(
            CaneAttitude.degText(tilt01deg.coerceIn(0, 900)),
            hx - (rr * sin(mid)).toFloat(), hy + (rr * cos(mid)).toFloat() + 8f * k, textPaint
        )
    }

    private companion object {
        const val DESIGN_W = 242f
        const val DESIGN_H = 362f
        const val HOLD_X = 175f
        const val HOLD_Y = 101f
        const val CANE_LEN = 200f
    }
}
