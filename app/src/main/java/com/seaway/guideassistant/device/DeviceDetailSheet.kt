package com.seaway.guideassistant.device

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.chip.Chip
import com.seaway.guideassistant.R
import com.seaway.guideassistant.ble.GlassesManager
import com.seaway.guideassistant.ble.cane.CaneBleClient
import com.seaway.guideassistant.ble.cane.CaneManager
import com.seaway.guideassistant.ble.cane.CaneProtocol
import com.seaway.guideassistant.databinding.SheetDeviceDetailBinding
import com.seaway.guideassistant.mock.DeviceInfo
import com.seaway.guideassistant.mock.MockDeviceRepository
import com.seaway.guideassistant.robot.RobotConnectionManager
import com.seaway.guideassistant.utils.announceA11y
import kotlinx.coroutines.launch

/**
 * 设备详情弹层，静态展示MockDeviceRepository数据，对应prototype的openDeviceSheet
 */
class DeviceDetailSheet : BottomSheetDialogFragment() {

    private var _binding: SheetDeviceDetailBinding? = null
    private val bind get() = _binding!!
    private var deviceId: String = ""

    companion object {
        private const val ARG_DEVICE_ID = "device_id"
        fun newInstance(deviceId: String) = DeviceDetailSheet().apply {
            arguments = Bundle().apply { putString(ARG_DEVICE_ID, deviceId) }
        }
    }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = SheetDeviceDetailBinding.inflate(inflater, container, false)
        return bind.root
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        deviceId = arguments?.getString(ARG_DEVICE_ID) ?: ""
        bind.btnClose.setOnClickListener { dismiss() }
        renderDevice()
        if (deviceId == "cane") observeCane()
    }

    /** 盲杖连接状态/电量变化时重绘；命令结果与错误以 Toast + 无障碍播报反馈 */
    private fun observeCane() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { CaneManager.uiState.collect { renderDevice() } }
                launch {
                    CaneManager.messages.collect { msg ->
                        Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show()
                        view?.announceA11y(msg)
                    }
                }
            }
        }
    }

    private fun renderDevice() {
        val device = MockDeviceRepository.findById(deviceId) ?: return dismiss()
        bind.tvIcon.text = device.icon
        bind.tvTitle.text = device.name
        bind.tvSn.text = if (device.connected) "SN: ${device.sn}" else device.sn

        if (device.connected) {
            bind.tvBadge.text = getString(R.string.device_status_connected)
            bind.tvBadge.setBackgroundResource(R.drawable.bg_badge_connected)
            if (device.hasBattery) {
                bind.tvBattery.visibility = View.VISIBLE
                bind.tvBattery.text = getString(R.string.device_battery_fmt, device.battery)
            } else {
                bind.tvBattery.visibility = View.GONE
            }
        } else {
            bind.tvBadge.text = getString(R.string.device_status_disconnected)
            bind.tvBadge.setBackgroundResource(R.drawable.bg_badge_disconnected)
            bind.tvBattery.visibility = View.GONE
        }

        bind.containerStats.removeAllViews()
        val dash = "—"
        addStatRow(getString(R.string.sheet_label_connection), if (device.connected) device.connection else dash)
        addStatRow(getString(R.string.sheet_label_signal), if (device.connected) device.signal else dash)
        addStatRow(getString(R.string.sheet_label_firmware), if (device.connected) device.firmware else dash)
        addStatRow(getString(R.string.sheet_label_sync), if (device.connected) device.lastSync else dash)

        bind.chipGroupFeatures.removeAllViews()
        for (feature in device.features) {
            val chip = Chip(requireContext())
            chip.text = feature
            chip.isClickable = false
            chip.isCheckable = false
            bind.chipGroupFeatures.addView(chip)
        }

        buildActions(device)

        view?.announceA11y(
            when {
                device.connected && device.hasBattery -> "${device.name}，已连接，电量${device.battery}%"
                device.connected -> "${device.name}，已连接"
                else -> "${device.name}，未连接"
            }
        )
    }

    private fun addStatRow(label: String, value: String) {
        val row = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                topMargin = resources.getDimensionPixelSize(R.dimen.dp_8)
            }
        }
        val tvLabel = TextView(requireContext()).apply {
            text = label
            setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text_muted))
            textSize = 13f
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        val tvValue = TextView(requireContext()).apply {
            text = value
            setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
            textSize = 14f
        }
        row.addView(tvLabel)
        row.addView(tvValue)
        bind.containerStats.addView(row)
    }

    private fun buildActions(device: DeviceInfo) {
        bind.containerActions.removeAllViews()
        val minTouch = resources.getDimensionPixelSize(R.dimen.min_touch_target)
        fun makeButton(text: String, onClick: () -> Unit): TextView = TextView(requireContext()).apply {
            this.text = text
            gravity = android.view.Gravity.CENTER
            isClickable = true
            isFocusable = true
            setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
            textSize = 15f
            setBackgroundResource(R.drawable.bg_btn_outline)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, minTouch).apply {
                topMargin = resources.getDimensionPixelSize(R.dimen.dp_10)
            }
            setOnClickListener { onClick() }
        }

        when {
            device.id == "dog" && !device.connected -> {
                buildRobotConnectForm()
            }
            device.id == "cane" -> buildCaneActions(device, ::makeButton)
            device.connected -> {
                bind.containerActions.addView(makeButton(getString(R.string.sheet_btn_disconnect)) {
                    when (device.id) {
                        "glasses" -> GlassesManager.disconnect()
                        "dog" -> {
                            RobotConnectionManager.disconnect()
                            MockDeviceRepository.unbindDog()
                        }
                    }
                    dismiss()
                })
                bind.containerActions.addView(makeButton(getString(R.string.sheet_btn_firmware_update)) {
                    view?.announceA11y(getString(R.string.common_coming_soon))
                })
            }
        }
    }

    /** 导盲杖：未连接显示连接按钮；已连接显示断开 + 震动 / 灯·标定控制面板（同 WhiteCane demo） */
    private fun buildCaneActions(device: DeviceInfo, makeButton: (String, () -> Unit) -> TextView) {
        val container = bind.containerActions
        if (!device.connected) {
            val busy = CaneManager.connState == CaneBleClient.ConnState.SCANNING ||
                CaneManager.connState == CaneBleClient.ConnState.CONNECTING
            val label = when (CaneManager.connState) {
                CaneBleClient.ConnState.SCANNING -> getString(R.string.cane_status_scanning)
                CaneBleClient.ConnState.CONNECTING -> getString(R.string.cane_status_connecting)
                else -> getString(R.string.cane_btn_connect)
            }
            container.addView(makeButton(label) {
                if (!busy) CaneManager.requestPermissionsAndConnect(requireActivity())
            }.apply { isEnabled = !busy })
            return
        }

        container.addView(makeButton(getString(R.string.sheet_btn_disconnect)) {
            CaneManager.disconnect()
        })

        addSectionTitle(getString(R.string.cane_section_vibration))
        addButtonRow(
            getString(R.string.cane_dir_front) to { CaneManager.buzz(CaneProtocol.MOTOR_FRONT, "前") },
            getString(R.string.cane_dir_right) to { CaneManager.buzz(CaneProtocol.MOTOR_RIGHT, "右") },
            getString(R.string.cane_dir_back) to { CaneManager.buzz(CaneProtocol.MOTOR_BACK, "后") },
            getString(R.string.cane_dir_left) to { CaneManager.buzz(CaneProtocol.MOTOR_LEFT, "左") },
        )
        addButtonRow(
            getString(R.string.cane_vib_all) to { CaneManager.buzz(CaneProtocol.MOTOR_ALL, "四路") },
            getString(R.string.cane_vib_stop) to { CaneManager.stopVibration() },
        )

        addSectionTitle(getString(R.string.cane_section_light_cal))
        addButtonRow(
            getString(R.string.cane_light_on) to { CaneManager.setLight(true) },
            getString(R.string.cane_light_off) to { CaneManager.setLight(false) },
        )
        addButtonRow(
            getString(R.string.cane_cal_start) to { CaneManager.startCalibration() },
            getString(R.string.cane_cal_abort) to { CaneManager.abortCalibration() },
        )
    }

    private fun addSectionTitle(title: String) {
        bind.containerActions.addView(TextView(requireContext()).apply {
            text = title
            setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text_muted))
            textSize = 13f
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                topMargin = resources.getDimensionPixelSize(R.dimen.dp_16)
            }
        })
    }

    /** 等宽的一行按钮 */
    private fun addButtonRow(vararg items: Pair<String, () -> Unit>) {
        val minTouch = resources.getDimensionPixelSize(R.dimen.min_touch_target)
        val gap = resources.getDimensionPixelSize(R.dimen.dp_8)
        val row = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                topMargin = gap
            }
        }
        items.forEachIndexed { i, (text, onClick) ->
            row.addView(TextView(requireContext()).apply {
                this.text = text
                gravity = android.view.Gravity.CENTER
                isClickable = true
                isFocusable = true
                setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
                textSize = 15f
                setBackgroundResource(R.drawable.bg_btn_outline)
                layoutParams = LinearLayout.LayoutParams(0, minTouch, 1f).apply {
                    if (i > 0) marginStart = gap
                }
                setOnClickListener { onClick() }
            })
        }
        bind.containerActions.addView(row)
    }

    private fun buildRobotConnectForm() {
        val form = layoutInflater.inflate(R.layout.view_robot_connect_form, bind.containerActions, true)
        val etHost = form.findViewById<EditText>(R.id.et_robot_host)
        val etPort = form.findViewById<EditText>(R.id.et_robot_port)
        val tvStatus = form.findViewById<TextView>(R.id.tv_robot_connect_status)
        val btnConnect = form.findViewById<TextView>(R.id.btn_robot_connect)

        etHost.setText(RobotConnectionManager.savedHost())
        val savedPort = RobotConnectionManager.savedPort()
        etPort.setText(if (savedPort > 0) savedPort.toString() else "")

        btnConnect.setOnClickListener {
            val host = etHost.text.toString().trim()
            val port = etPort.text.toString().trim().toIntOrNull()
            if (host.isEmpty() || port == null) {
                tvStatus.text = getString(R.string.robot_status_invalid_input)
                return@setOnClickListener
            }
            tvStatus.text = getString(R.string.robot_status_connecting)
            RobotConnectionManager.connect(host, port)
        }

        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                RobotConnectionManager.connectionState.collect { state ->
                    when (state) {
                        is RobotConnectionManager.ConnectionState.Idle -> tvStatus.text = ""
                        is RobotConnectionManager.ConnectionState.Connecting -> tvStatus.text = getString(R.string.robot_status_connecting)
                        is RobotConnectionManager.ConnectionState.Connected -> {
                            tvStatus.text = getString(R.string.robot_status_connected)
                            if (!MockDeviceRepository.dog.connected) {
                                MockDeviceRepository.bindDog()
                                view?.announceA11y(getString(R.string.sheet_bind_dog_success))
                                renderDevice()
                            }
                        }
                        is RobotConnectionManager.ConnectionState.Failed -> {
                            tvStatus.text = getString(R.string.robot_status_failed_fmt, state.reason)
                            view?.announceA11y(getString(R.string.robot_status_failed_fmt, state.reason))
                        }
                    }
                }
            }
        }
    }
}
