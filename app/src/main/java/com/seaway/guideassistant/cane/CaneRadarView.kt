package com.seaway.guideassistant.cane

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import com.seaway.guideassistant.R
import com.seaway.guideassistant.ble.cane.CaneAttitude
import com.seaway.guideassistant.ble.cane.CaneProtocol
import kotlin.math.cos
import kotlin.math.sin

/**
 * 摆动雷达（设计稿 463×429）：上半圆。指针角度 = sector（0 最左 / 900 正前 / 1800 最右），
 * 指针长度 = reach；底部胶囊显示当前水平距离；碰撞时在指针顶部播放 #00DBFF 波纹，
 * 时长/强度由 peak 等级决定。所有尺寸按控件宽度等比换算，随屏幕自适应。
 */
class CaneRadarView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : View(context, attrs, defStyle) {

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    private val ripplePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; color = RIPPLE_COLOR }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
    private val rect = RectF()

    private var sector = CaneProtocol.SECTOR_INVALID
    private var reachCm: Int? = null
    private var maxReachCm = DEFAULT_MAX_REACH_CM
    private var rippleLevel = 0
    private var ripple = 0f   // 0..1，0 表示没有在播放

    private var animator: ValueAnimator? = null

    fun update(sector01deg: Int, reach: Int?, peak: Int, state: Int, maxReach: Int) {
        sector = sector01deg
        reachCm = reach
        maxReachCm = if (maxReach > 0) maxReach else DEFAULT_MAX_REACH_CM
        contentDescription = context.getString(
            R.string.cane_cd_radar_fmt,
            CaneAttitude.sectorToRadians(sector01deg)?.let { CaneAttitude.degText(sector01deg) } ?: "--",
            reach?.toString() ?: "--"
        )
        val level = CaneAttitude.peakLevel(peak)
        if (state == CaneProtocol.ST_COLLIDING && level > 0) startRipple(level)
        invalidate()
    }

    private fun startRipple(level: Int) {
        // 动画进行中只在更强的碰撞到来时重启，避免每帧 DATA 都重置动画而看不到扩散
        if (animator?.isRunning == true && level <= rippleLevel) return
        animator?.cancel()
        rippleLevel = level
        animator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = CaneAttitude.rippleDurationMs(level)
            interpolator = DecelerateInterpolator()
            addUpdateListener { ripple = it.animatedValue as Float; invalidate() }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(a: Animator) { ripple = 0f; invalidate() }
                override fun onAnimationCancel(a: Animator) { ripple = 0f }
            })
            start()
        }
    }

    override fun onDetachedFromWindow() {
        animator?.cancel()
        super.onDetachedFromWindow()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        setMeasuredDimension(w, (w * DESIGN_H / DESIGN_W).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val k = width / DESIGN_W
        val cx = CX * k
        val cy = CY * k
        val r = RADIUS * k

        // 半圆底盘：半透明填充 + 外圈发光描边
        fillPaint.color = 0x6B003355
        rect.set(cx - r, cy - r, cx + r, cy + r)
        canvas.drawArc(rect, 180f, 180f, true, fillPaint)
        strokePaint.color = (RIPPLE_COLOR and 0x00FFFFFF) or 0x40000000
        strokePaint.strokeWidth = 9f * k
        canvas.drawArc(rect, 180f, 180f, false, strokePaint)
        strokePaint.color = 0xFF00E1F7.toInt()
        strokePaint.strokeWidth = 2.5f * k
        canvas.drawArc(rect, 180f, 180f, false, strokePaint)

        // 同心刻度圈 + 放射线
        strokePaint.color = (0x8BEAFF and 0xFFFFFF) or 0x80000000.toInt()
        strokePaint.strokeWidth = 1.2f * k
        for (i in 1..3) {
            val rr = r * i / 4f
            rect.set(cx - rr, cy - rr, cx + rr, cy + rr)
            canvas.drawArc(rect, 180f, 180f, false, strokePaint)
        }
        canvas.drawLine(cx - r, cy, cx + r, cy, strokePaint)
        for (i in 1..7) {
            val a = Math.toRadians(i * 22.5)
            canvas.drawLine(cx, cy, cx + (r * cos(a)).toFloat(), cy - (r * sin(a)).toFloat(), strokePaint)
        }

        // 端点与刻度文字
        fillPaint.color = Color.WHITE
        canvas.drawCircle(cx - r, cy, 5f * k, fillPaint)
        canvas.drawCircle(cx + r, cy, 5f * k, fillPaint)
        canvas.drawCircle(cx, cy - r, 5f * k, fillPaint)
        textPaint.typeface = Typeface.DEFAULT
        textPaint.textSize = 22f * k
        textPaint.textAlign = Paint.Align.CENTER
        canvas.drawText("0", 6.5f * k, 277f * k, textPaint)
        canvas.drawText("1800", 438f * k, 277f * k, textPaint)
        textPaint.textSize = 25f * k
        canvas.drawText("900", cx, 62f * k, textPaint)

        // 指针：sector 无效 / reach 无效 时不画（保持半圆底盘）
        val ang = CaneAttitude.sectorToRadians(sector)
        val reach = reachCm
        if (ang != null && reach != null) {
            val len = r * (reach.toFloat() / maxReachCm).coerceIn(0f, 1f)
            val tx = cx + (len * cos(ang)).toFloat()
            val ty = cy - (len * sin(ang)).toFloat()
            strokePaint.color = 0x40FFFFFF
            strokePaint.strokeWidth = 10f * k
            canvas.drawLine(cx, cy, tx, ty, strokePaint)
            strokePaint.color = 0xFFF8FFFF.toInt()
            strokePaint.strokeWidth = 4f * k
            canvas.drawLine(cx, cy, tx, ty, strokePaint)
            fillPaint.color = RIPPLE_COLOR
            canvas.drawCircle(tx, ty, 10.5f * k, fillPaint)
            strokePaint.color = Color.WHITE
            strokePaint.strokeWidth = 3f * k
            canvas.drawCircle(tx, ty, 10.5f * k, strokePaint)
            drawRipple(canvas, tx, ty, r, k)
        }
        fillPaint.color = Color.WHITE
        canvas.drawCircle(cx, cy, 7f * k, fillPaint)

        // 底部胶囊：当前水平距离
        rect.set(43f * k, 377f * k, 412f * k, 429f * k)
        fillPaint.color = 0xFF001525.toInt()
        canvas.drawRoundRect(rect, 26f * k, 26f * k, fillPaint)
        strokePaint.color = 0xFF00E1F8.toInt()
        strokePaint.strokeWidth = 1.3f * k
        canvas.drawRoundRect(rect, 26f * k, 26f * k, strokePaint)
        textPaint.typeface = Typeface.DEFAULT_BOLD
        textPaint.textAlign = Paint.Align.LEFT
        textPaint.color = Color.WHITE
        textPaint.textSize = 27f * k
        canvas.drawText(context.getString(R.string.cane_stat_reach), 81f * k, 413f * k, textPaint)
        textPaint.color = 0xFF00DBFA.toInt()
        textPaint.textSize = 33f * k
        canvas.drawText(if (reach != null) "$reach cm" else "-- cm", 262f * k, 413f * k, textPaint)
        textPaint.color = Color.WHITE
    }

    private fun drawRipple(canvas: Canvas, x: Float, y: Float, r: Float, k: Float) {
        if (ripple <= 0f || rippleLevel <= 0) return
        val rings = CaneAttitude.rippleRings(rippleLevel)
        val maxR = r * CaneAttitude.rippleRadiusFraction(rippleLevel) * 2f
        ripplePaint.strokeWidth = width * (0.006f + 0.002f * rippleLevel)
        for (i in 0 until rings) {
            // 每圈错开起点，形成连续向外扩散
            val p = ripple - i * 0.18f
            if (p <= 0f || p > 1f) continue
            ripplePaint.alpha = ((1f - p) * 255).toInt().coerceIn(0, 255)
            canvas.drawCircle(x, y, maxR * p + 10.5f * k, ripplePaint)
        }
        // 实心碰撞核心，随动画淡出
        fillPaint.color = RIPPLE_COLOR
        fillPaint.alpha = ((1f - ripple) * 200).toInt().coerceIn(0, 255)
        canvas.drawCircle(x, y, width * (0.02f + 0.006f * rippleLevel), fillPaint)
        fillPaint.alpha = 255
    }

    companion object {
        /** 碰撞动画色值 #00DBFF */
        val RIPPLE_COLOR: Int = Color.parseColor("#00DBFF")
        private const val DEFAULT_MAX_REACH_CM = 150

        // 设计稿坐标系（03a_雷达仪表 463×429）：半圆圆心 (217,268)，半径 188
        private const val DESIGN_W = 463f
        private const val DESIGN_H = 429f
        private const val CX = 217f
        private const val CY = 268f
        private const val RADIUS = 188f
    }
}
