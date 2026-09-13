package com.seaway.guideassistant.fragment

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import androidx.core.content.ContextCompat
import com.google.android.material.chip.Chip
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.databinding.FragmentObstacleBinding
import com.seaway.guideassistant.mock.MockNavRepository
import com.seaway.guideassistant.utils.announceA11y

class ObstacleFragment : BaseBindFragment<FragmentObstacleBinding>() {

    private val handler = Handler(Looper.getMainLooper())
    private var running = false
    private var currentIndex = 0
    private val scenarios = MockNavRepository.obstacleScenarios

    private val cycleRunnable = object : Runnable {
        override fun run() {
            currentIndex = (currentIndex + 1) % scenarios.size
            renderScenario()
            handler.postDelayed(this, 4_000)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        bind.btnToggle.setOnClickListener { if (running) stopObstacle() else startObstacle() }
    }

    override fun onDestroyView() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroyView()
    }

    private fun startObstacle() {
        running = true
        currentIndex = 0
        bind.tvStatus.text = getString(R.string.obstacle_status_running)
        bind.btnToggle.text = getString(R.string.obstacle_btn_stop)
        bind.panelScenario.visibility = View.VISIBLE
        renderScenario()
        bind.root.announceA11y(getString(R.string.obstacle_started_toast))
        handler.postDelayed(cycleRunnable, 4_000)
    }

    private fun stopObstacle() {
        running = false
        handler.removeCallbacks(cycleRunnable)
        bind.tvStatus.text = getString(R.string.obstacle_status_off)
        bind.btnToggle.text = getString(R.string.obstacle_btn_start)
        bind.panelScenario.visibility = View.GONE
        bind.root.announceA11y(getString(R.string.obstacle_stopped_toast))
    }

    private fun renderScenario() {
        val scenario = scenarios[currentIndex]
        bind.tvScenarioTitle.text = scenario.title
        bind.tvScenarioDesc.text = scenario.desc
        bind.tvVibDir.text = MockNavRepository.vibDirLabel(scenario.dir)
        bind.chipGroupTags.removeAllViews()
        scenario.tags.forEach { tag ->
            val chip = Chip(requireContext())
            chip.text = tag
            chip.isClickable = false
            chip.isCheckable = false
            chip.setChipBackgroundColorResource(R.color.color_primary_dim)
            chip.setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
            bind.chipGroupTags.addView(chip)
        }
    }
}
