package com.seaway.guideassistant.fragment

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.widget.TextView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.chip.Chip
import com.lsxiao.apollo.core.Apollo
import com.orhanobut.logger.Logger
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.base.Constant.LLM_API_KEY
import com.seaway.guideassistant.bean.IntentResponse
import com.seaway.guideassistant.databinding.FragmentAgentBinding
import com.seaway.guideassistant.http.bindLife
import com.seaway.guideassistant.http.im
import com.seaway.guideassistant.llm.IntentRecognizerConfig
import com.seaway.guideassistant.llm.NavigationIntentEncoder
import com.seaway.guideassistant.llm.QwenIntentRecognizer
import com.seaway.guideassistant.mock.MockAgentRepository
import com.seaway.guideassistant.navigation.NavigationPlanManager
import com.seaway.guideassistant.robot.RobotConnectionManager
import com.seaway.guideassistant.utils.ApolloEvents
import com.seaway.guideassistant.utils.announceA11y
import com.seaway.smallutils.TtsUtils
import com.seaway.guideassistant.voice.VoiceErrorReason
import com.seaway.guideassistant.voice.VoiceInputController
import com.seaway.smallutils.ToastUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import org.koin.android.ext.android.get
import org.koin.core.qualifier.named

class AgentFragment : BaseBindFragment<FragmentAgentBinding>() {

    private var voiceController: VoiceInputController? = null

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        buildQuickPhrases()
        addMessage(getString(R.string.cd_msg_avatar_agent), getString(R.string.agent_wake_reply), isAgent = true)
        bind.btnSend.setOnClickListener { sendCurrentInput() }
        voiceController = VoiceInputController(requireActivity(), get<OkHttpClient>(named("webSocket")), object : VoiceInputController.Callbacks {
            override fun onListeningStarted() = updateVoiceButton(recording = true)
            override fun onTranscript(text: String) = handleTranscript(text)
            override fun onError(reason: VoiceErrorReason) = handleVoiceError(reason)
        })
        bind.btnVoice.setOnClickListener { voiceController?.toggle() }
        observeRobotStatus()
    }

    /** 展示机器狗连接状态，指令下发统一在 NavigateFragment 侧触发，这里只做只读展示 */
    private fun observeRobotStatus() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                RobotConnectionManager.connectionState.collect { state ->
                    bind.tvRobotStatus.text = when (state) {
                        is RobotConnectionManager.ConnectionState.Idle -> getString(R.string.robot_status_disconnected)
                        is RobotConnectionManager.ConnectionState.Connecting -> getString(R.string.robot_status_connecting)
                        is RobotConnectionManager.ConnectionState.Connected -> getString(R.string.robot_status_connected)
                        is RobotConnectionManager.ConnectionState.Failed -> getString(R.string.robot_status_failed_fmt, state.reason)
                    }
                }
            }
        }
    }

    override fun onDestroyView() {
        voiceController?.release()
        voiceController = null
        super.onDestroyView()
    }

    private fun updateVoiceButton(recording: Boolean) {
        bind.btnVoice.setBackgroundResource(if (recording) R.drawable.bg_btn_recording else R.drawable.bg_btn_outline)
        bind.btnVoice.contentDescription = getString(if (recording) R.string.cd_agent_voice_btn_recording else R.string.cd_agent_voice_btn)
        if (recording) bind.root.announceA11y(getString(R.string.agent_voice_listening))
    }

    @Suppress("CheckResult")
    private fun handleTranscript(text: String) {
        updateVoiceButton(recording = false)
        sendPhrase(text)
//        dataProvider.intent.recognize(text).im().bindLife(provider).subscribe({ handleIntentResult(it) }, {})
        demoIntentRecognition(LLM_API_KEY,text)
    }

    private fun demoIntentRecognition(apiKey: String, text: String) {
        val recognizer = QwenIntentRecognizer(IntentRecognizerConfig(apiKey = apiKey))

        viewLifecycleOwner.lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) { recognizer.parse(text) }
            } catch (e: Exception) {
                Logger.i("LLm intent recognition failed: ${e.message}")
                return@launch
            }
            Logger.i("LLm recognizer = ${NavigationIntentEncoder.toJson(result)}")

            if (result.needClarification) {
                val question = result.clarificationQuestion
                if (!question.isNullOrBlank()) {
                    addMessage(getString(R.string.cd_msg_avatar_agent), question, isAgent = true)
                }
                return@launch
            }

            NavigationPlanManager.startPlan(result, text)
        }
    }

    private fun handleIntentResult(result: IntentResponse) {
        if (result.intent == "navigate") {
            Apollo.emit(ApolloEvents.SWITCH_TO_NAVIGATE_TAB)
        }
    }

    private fun handleVoiceError(reason: VoiceErrorReason) {
        updateVoiceButton(recording = false)
        val message = when (reason) {
            VoiceErrorReason.PERMISSION_DENIED -> getString(R.string.agent_voice_error_permission_denied)
            VoiceErrorReason.NO_SPEECH -> getString(R.string.agent_voice_error_no_speech)
            VoiceErrorReason.CONNECTION_FAILED -> getString(R.string.agent_voice_error_connection_failed)
            VoiceErrorReason.RECORDING_FAILED -> getString(R.string.agent_voice_error_recording_failed)
        }
        bind.root.announceA11y(message)
        ToastUtil.showShort(message)
        TtsUtils.speak(message)
    }

    private fun buildQuickPhrases() {
        bind.chipGroupQuick.removeAllViews()
        MockAgentRepository.quickPhrases.forEach { phrase ->
            val chip = Chip(requireContext())
            chip.text = phrase
            chip.setChipBackgroundColorResource(R.color.color_surface_2)
            chip.setOnClickListener { sendPhrase(phrase) }
            bind.chipGroupQuick.addView(chip)
        }
    }

    private fun sendCurrentInput() {
        val text = bind.etInput.text.toString().trim()
        if (text.isEmpty()) return
        bind.etInput.setText("")
        sendPhrase(text)
        demoIntentRecognition(LLM_API_KEY,text)
    }

    private fun sendPhrase(text: String) {
        addMessage(getString(R.string.cd_msg_avatar_user), text, isAgent = false)
        val reply = MockAgentRepository.reply(text)
        addMessage(getString(R.string.cd_msg_avatar_agent), reply, isAgent = true)
    }

    private fun addMessage(sender: String, text: String, isAgent: Boolean) {
        val itemView = LayoutInflater.from(context).inflate(R.layout.item_chat_message, bind.containerMessages, false)
        itemView.findViewById<TextView>(R.id.tv_sender).text = sender
        itemView.findViewById<TextView>(R.id.tv_text).text = text
        val root = itemView.findViewById<android.widget.LinearLayout>(R.id.root)
        val params = root.layoutParams as android.widget.LinearLayout.LayoutParams
        params.gravity = if (isAgent) android.view.Gravity.START else android.view.Gravity.END
        root.layoutParams = params
        if (!isAgent) {
            itemView.findViewById<TextView>(R.id.tv_text).setBackgroundResource(R.drawable.bg_card_active)
        }
        itemView.contentDescription = "$sender：$text"
        bind.containerMessages.addView(itemView)
        bind.scrollMessages.post { bind.scrollMessages.fullScroll(View.FOCUS_DOWN) }
        bind.root.announceA11y(text)
        if (isAgent) {
            TtsUtils.speak(text)
        }
    }
}
