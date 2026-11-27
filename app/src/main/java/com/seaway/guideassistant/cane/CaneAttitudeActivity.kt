package com.seaway.guideassistant.cane

import android.os.Bundle
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.view.View
import android.widget.LinearLayout
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindActivity
import com.seaway.guideassistant.ble.cane.CaneAttitude
import com.seaway.guideassistant.ble.cane.CaneManager
import com.seaway.guideassistant.ble.cane.CaneProtocol
import com.seaway.guideassistant.databinding.ActivityCaneAttitudeBinding
import com.seaway.guideassistant.databinding.ItemCaneStateRowBinding
import com.seaway.smallutils.text.NavigationBar
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/** 实时查看盲杖姿态数据：把 DATA 帧的 tilt/sector/reach/orientation/body_facing/state/peak 全部可视化。 */
class CaneAttitudeActivity : BaseBindActivity<ActivityCaneAttitudeBinding>() {

    private val stateRows = mutableListOf<ItemCaneStateRowBinding>()

    override fun onCreate(savedInstanceState: Bundle?, bar: NavigationBar) {
        bar.setTitle(getString(R.string.cane_attitude_title))
        bar.setBackgroundColor(ContextCompat.getColor(this, R.color.cane_bg), false)
        bar.titleView?.setTextColor(ContextCompat.getColor(this, R.color.white))
        bar.titleView?.setTypeface(null, Typeface.BOLD)
        bar.backImageView?.setColorFilter(ContextCompat.getColor(this, R.color.cane_cyan))
        buildStateRows()

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                CaneManager.data.combine(CaneManager.caneState) { d, s -> d to s }
                    .collect { (d, s) -> render(d, s) }
            }
        }
    }

    /** 状态表：圆点颜色 / 序号 / 名称 / 说明，行间 1dp 分隔线，对应设计稿 05_状态表。 */
    private fun buildStateRows() {
        val descs = resources.getStringArray(R.array.cane_state_descs)
        val dotColors = intArrayOf(
            R.color.cane_state_idle, R.color.cane_state_swing, R.color.cane_state_swing,
            R.color.cane_state_collide, R.color.cane_state_calib, R.color.cane_state_pit
        )
        CaneAttitude.STATE_NAMES.forEachIndexed { i, name ->
            if (i > 0) {
                bind.containerStates.addView(View(this).apply {
                    setBackgroundColor(0x6642677E)
                    layoutParams = LinearLayout.LayoutParams(-1, 1)
                })
            }
            val row = ItemCaneStateRowBinding.inflate(layoutInflater, bind.containerStates, false)
            row.vDot.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, dotColors[i]))
            row.tvIndex.text = i.toString()
            row.tvName.text = name
            row.tvDesc.text = descs.getOrElse(i) { "" }
            bind.containerStates.addView(row.root)
            stateRows += row
        }
        highlightState(-1)
    }

    private fun highlightState(state: Int) {
        stateRows.forEachIndexed { i, row ->
            row.root.setBackgroundResource(if (i == state) R.drawable.bg_cane_row_active else 0)
        }
    }

    private fun render(d: CaneProtocol.Data?, s: CaneProtocol.State?) {
        bind.tvOffline.visibility = if (d == null) View.VISIBLE else View.GONE
        if (d == null) {
            // 数据为空：要么盲杖已断开，要么刚连上还没收到第一帧
            bind.tvOffline.text = getString(
                if (CaneManager.isReady) R.string.cane_attitude_waiting else R.string.cane_attitude_disconnected
            )
            bind.radar.update(CaneProtocol.SECTOR_INVALID, null, 0, CaneProtocol.ST_IDLE, 0)
            bind.compass.update(null)
            bind.tilt.update(0)
            bind.peakDots.update(0, null)
            listOf(bind.tvSector, bind.tvReach, bind.tvState).forEach { it.text = "--" }
            highlightState(-1)
            return
        }
        val reach = CaneAttitude.reachOrNull(d.reachCm)
        val level = CaneAttitude.peakLevel(d.peak)

        bind.radar.update(d.sector01deg, reach, d.peak, d.state, s?.caneLenCm ?: 0)
        bind.compass.update(CaneAttitude.orientationOrNull(d))
        bind.tilt.update(d.tilt01deg)

        bind.tvSector.text = if (d.sectorValid) CaneAttitude.degText(d.sector01deg) else "--"
        bind.tvReach.text = reach?.let { "$it cm" } ?: "--"
        bind.peakDots.update(
            level,
            if (level == 0) CaneAttitude.LEVEL_NAMES[0]
            else getString(R.string.cane_peak_fmt, level, CaneAttitude.LEVEL_NAMES[level], d.peak)
        )
        bind.tvState.text = CaneAttitude.stateName(d.state)
        highlightState(d.state)
    }
}
