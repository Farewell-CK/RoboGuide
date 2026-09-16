package com.seaway.guideassistant.fragment

import android.annotation.SuppressLint
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.viewpager2.widget.ViewPager2
import com.moyoung.glasses.conn.CRPBleConnection
import com.moyoung.glasses.conn.listener.CRPWifiChangeListener
import com.moyoung.glasses.conn.type.CRPWifiType
import com.seaway.guideassistant.R
import com.seaway.guideassistant.auth.LoginActivity
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.ble.GlassesManager
import com.seaway.guideassistant.databinding.FragmentHomeBinding
import com.seaway.guideassistant.device.DeviceDetailSheet
import com.seaway.guideassistant.mock.DeviceInfo
import com.seaway.guideassistant.mock.MockDeviceRepository
import com.seaway.guideassistant.robot.RobotConnectionManager
import com.seaway.guideassistant.utils.SessionManager
import com.seaway.guideassistant.utils.announceA11y
import com.seaway.guideassistant.ws.DeviceType
import com.seaway.guideassistant.ws.DeviceWatchClient
import kotlinx.coroutines.launch

class HomeFragment : BaseBindFragment<FragmentHomeBinding>() {

    private var connection: CRPBleConnection? = null
    private var liveUrl: String? = null
    private var isLiveActive = false
    private val TAG = "GlassSDK"

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        bind.tvGreeting.text = getString(R.string.home_greeting_fmt, SessionManager.getUserName().ifEmpty { "用户" })
        buildDeviceCards()
        bindActions()
        observeGlasses()
        observeRobotConnection()
    }

    /** 与 DeviceDetailSheet/DevicesFragment 共享同一个 RobotConnectionManager 状态源，保持机器狗连接状态实时同步 */
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
                    reportRobotStatus(state)
                    buildDeviceCards()
                }
            }
        }
    }

    /** 订阅 GlassesManager 广播的扫描/连接状态与电量，同步到首页 UI */
    private fun observeGlasses() {
        bind.tvStatus.setOnClickListener {
            val state = GlassesManager.connectionState.value
            if (state is GlassesManager.ConnectionState.PermissionDenied || state is GlassesManager.ConnectionState.Failed) {
                GlassesManager.requestPermissionsAndScan(requireActivity())
            }
        }
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { GlassesManager.connectionState.collect { updateGlassesStatus(it) } }
                launch { GlassesManager.batteryState.collect { updateGlassesBattery(it) } }
            }
        }
    }

    /** 上报机器狗连接状态给设备监控 watch 服务 */
    private fun reportRobotStatus(state: RobotConnectionManager.ConnectionState) {
        val status: String
        val reason: String?
        when (state) {
            is RobotConnectionManager.ConnectionState.Idle -> { status = "offline"; reason = null }
            is RobotConnectionManager.ConnectionState.Connecting -> { status = "connecting"; reason = null }
            is RobotConnectionManager.ConnectionState.Connected -> { status = "online"; reason = null }
            is RobotConnectionManager.ConnectionState.Failed -> { status = "error"; reason = state.reason }
        }
        DeviceWatchClient.reportDeviceStatus(
            deviceType = DeviceType.ROBOT_DOG,
            deviceName = MockDeviceRepository.dog.name,
            status = status,
            reason = reason
        )
    }

    /** 上报导盲眼镜连接状态给设备监控 watch 服务 */
    private fun reportGlassesStatus(state: GlassesManager.ConnectionState) {
        val status: String
        val reason: String?
        when (state) {
            GlassesManager.ConnectionState.Idle -> return
            GlassesManager.ConnectionState.Scanning,
            GlassesManager.ConnectionState.Connecting -> { status = "connecting"; reason = null }
            GlassesManager.ConnectionState.Connected -> { status = "online"; reason = null }
            GlassesManager.ConnectionState.Disconnected -> { status = "offline"; reason = null }
            GlassesManager.ConnectionState.PermissionDenied -> { status = "error"; reason = "蓝牙权限被拒绝" }
            is GlassesManager.ConnectionState.Reconnecting -> { status = "connecting"; reason = "重连中(${state.attempt}/${state.maxAttempts})" }
            is GlassesManager.ConnectionState.Failed -> { status = "error"; reason = state.reason }
        }
        DeviceWatchClient.reportDeviceStatus(
            deviceType = DeviceType.GLASSES,
            deviceName = MockDeviceRepository.glasses.name,
            status = status,
            battery = GlassesManager.batteryState.value?.level,
            reason = reason
        )
    }

    private fun updateGlassesStatus(state: GlassesManager.ConnectionState) {
        reportGlassesStatus(state)
        bind.tvStatus.text = when (state) {
            GlassesManager.ConnectionState.Idle -> ""
            GlassesManager.ConnectionState.Scanning -> getString(R.string.glasses_status_scanning)
            GlassesManager.ConnectionState.Connecting -> getString(R.string.glasses_status_connecting)
            GlassesManager.ConnectionState.Connected -> getString(R.string.glasses_status_connected)
            GlassesManager.ConnectionState.Disconnected -> getString(R.string.glasses_status_disconnected)
            GlassesManager.ConnectionState.PermissionDenied -> getString(R.string.glasses_status_permission_denied)
            is GlassesManager.ConnectionState.Reconnecting -> getString(R.string.glasses_status_reconnecting_fmt, state.attempt, state.maxAttempts)
            is GlassesManager.ConnectionState.Failed -> state.reason
        }
        bind.tvStatus.isClickable = state is GlassesManager.ConnectionState.PermissionDenied || state is GlassesManager.ConnectionState.Failed

        when (state) {
            GlassesManager.ConnectionState.Connected -> {
                connection = GlassesManager.currentConnection()
                connection?.setWifiListener(liveWifiListener)
            }
            GlassesManager.ConnectionState.Disconnected, is GlassesManager.ConnectionState.Reconnecting -> connection = null
            else -> Unit
        }
    }

    private fun updateGlassesBattery(state: GlassesManager.BatteryState?) {
        MockDeviceRepository.glasses.connected = state != null
        MockDeviceRepository.glasses.battery = state?.level ?: MockDeviceRepository.glasses.battery
        buildDeviceCards()
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

    private fun bindActions() {
        bind.actionNavigate.setOnClickListener { goToTab(1) }
        bind.actionObstacle.setOnClickListener { goToTab(3) }
        bind.actionAgent.setOnClickListener { goToTab(2) }
        bind.actionDevices.setOnClickListener { goToTab(4) }
        bind.btnLogout.setOnClickListener {
            SessionManager.clearSession()
            goActivity(LoginActivity::class.java)
            requireActivity().finish()
        }
        bind.btnVoiceFab.setOnClickListener {
            bind.root.announceA11y(getString(R.string.common_coming_soon))
        }
    }

    private fun goToTab(index: Int) {
        requireActivity().findViewById<ViewPager2>(R.id.view_pager)?.currentItem = index
    }

    // Wi-Fi 直播相关监听，眼镜连接成功后挂到 GlassesManager 提供的 connection 上
    private val liveWifiListener = object : CRPWifiChangeListener {
        override fun onWifiStateChange(type: CRPWifiType, state: Int) {
            if (type == CRPWifiType.LIVE) {
                if (state == CRPWifiChangeListener.STATE_SUCCESS) {
                    connection?.connectWifi()
                    Log.e(TAG, "直播 Wi-Fi 开启成功: $state")
                } else {
                    Log.e(TAG, "直播 Wi-Fi 开启失败: $state")
                }
            }
        }

        override fun onWifiConnectionStateChanged(connected: Boolean) {
            isLiveActive = connected
            Log.d(TAG, "直播状态: " + (if (connected) "已连接" else "已断开"))
        }

        @SuppressLint("UnsafeOptInUsageError")
        override fun onLiveUrlChanged(url: String) {
            // 直播链接回调，视频流格式：rtsp
            liveUrl = url
            Log.d(TAG, "直播地址: $url")
//            try {
//                activity?.runOnUiThread {
//                    val mediaItem = MediaItem.fromUri(url)
//                    player.setMediaItem(mediaItem)
//                    player.prepare()
//                    player.play()
//                }
//            } catch (e: Exception) {
//                Log.e("RTSP", "setMediaSource FAILED", e)
//            }
        }
    }

    // 时间同步
    fun syncTime() {
        connection?.syncTime()
        Log.d(TAG, "时间已同步 / Time synced")
    }

    fun startLive() {
    }

    fun stopLive() {
        connection?.stopLive()
        connection?.disableWifi()
        isLiveActive = false
        liveUrl = null
//        player.stop()
    }
}
