package com.seaway.guideassistant.fragment

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.widget.TextView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.databinding.FragmentDevicesBinding
import com.seaway.guideassistant.device.DeviceDetailSheet
import com.seaway.guideassistant.mock.DeviceInfo
import com.seaway.guideassistant.mock.MockDeviceRepository
import com.seaway.guideassistant.robot.RobotConnectionManager
import kotlinx.coroutines.launch

class DevicesFragment : BaseBindFragment<FragmentDevicesBinding>() {

    private val handler = Handler(Looper.getMainLooper())

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        buildDeviceCards()
        bind.btnScan.setOnClickListener { startScan() }
        observeRobotConnection()
    }

    /** 与 DeviceDetailSheet/HomeFragment 共享同一个 RobotConnectionManager 状态源，保持机器狗连接状态实时同步 */
    private fun observeRobotConnection() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                RobotConnectionManager.connectionState.collect { state ->
                    when (state) {
                        is RobotConnectionManager.ConnectionState.Connected -> MockDeviceRepository.bindDog()
                        is RobotConnectionManager.ConnectionState.Idle,
                        is RobotConnectionManager.ConnectionState.Failed -> MockDeviceRepository.unbindDog()
                        is RobotConnectionManager.ConnectionState.Connecting -> Unit
                    }
                    buildDeviceCards()
                }
            }
        }
    }

    override fun onDestroyView() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroyView()
    }

    private fun startScan() {
        bind.tvStatus.text = getString(R.string.devices_scan_hint)
        handler.postDelayed({
            if (isAdded) bind.tvStatus.text = getString(R.string.devices_scan_found)
        }, 3000)
    }

    private fun buildDeviceCards() {
        bind.containerDevices.removeAllViews()
        for (device in MockDeviceRepository.all) {
            val itemView = LayoutInflater.from(context).inflate(R.layout.item_device_card, bind.containerDevices, false)
            bindDeviceCard(itemView, device)
            bind.containerDevices.addView(itemView)
        }
    }

    private fun bindDeviceCard(itemView: View, device: DeviceInfo) {
        itemView.findViewById<TextView>(R.id.tv_icon).text = device.icon
        itemView.findViewById<TextView>(R.id.tv_name).text = device.name
        val tvStatus = itemView.findViewById<TextView>(R.id.tv_status)
        val tvBattery = itemView.findViewById<TextView>(R.id.tv_battery)
        if (device.connected) {
            tvStatus.text = getString(R.string.device_status_connected)
            tvStatus.setBackgroundResource(R.drawable.bg_badge_connected)
            if (device.hasBattery) {
                tvBattery.visibility = View.VISIBLE
                tvBattery.text = getString(R.string.device_battery_fmt, device.battery)
            } else {
                tvBattery.visibility = View.GONE
            }
        } else {
            tvStatus.text = getString(R.string.device_status_disconnected)
            tvStatus.setBackgroundResource(R.drawable.bg_badge_disconnected)
            tvBattery.visibility = View.GONE
        }
        val statusText = if (device.connected) getString(R.string.device_status_connected) else getString(R.string.device_status_disconnected)
        itemView.contentDescription = getString(R.string.cd_device_card_fmt, device.name, statusText)
        itemView.setOnClickListener {
            DeviceDetailSheet.newInstance(device.id).show(childFragmentManager, "device_detail")
        }
    }
}
