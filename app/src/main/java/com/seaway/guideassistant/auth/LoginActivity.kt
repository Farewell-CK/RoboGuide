package com.seaway.guideassistant.auth

import android.os.Bundle
import android.os.CountDownTimer
import android.widget.TextView
import com.seaway.smallutils.text.NavigationBar
import com.seaway.guideassistant.MainActivity
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindActivity
import com.seaway.guideassistant.databinding.ActivityLoginBinding
import com.seaway.guideassistant.utils.SessionManager
import com.seaway.guideassistant.utils.announceA11y

/**
 * 登录/注册页，仅使用本地模拟校验，不发起真实网络请求（对应prototype/app.js的doLogin/sendCode）
 */
class LoginActivity : BaseBindActivity<ActivityLoginBinding>() {

    override fun onCreate(savedInstanceState: Bundle?, bar: NavigationBar) {
        bar.hide()
        bind.btnTabLogin.isSelected = true
        initTabs()
        initActions()
    }

    private fun initTabs() {
        bind.btnTabLogin.setOnClickListener { switchTab(isLogin = true) }
        bind.btnTabRegister.setOnClickListener { switchTab(isLogin = false) }
    }

    private fun switchTab(isLogin: Boolean) {
        bind.btnTabLogin.isSelected = isLogin
        bind.btnTabRegister.isSelected = !isLogin
        bind.groupLogin.visibility = if (isLogin) android.view.View.VISIBLE else android.view.View.GONE
        bind.groupRegister.visibility = if (isLogin) android.view.View.GONE else android.view.View.VISIBLE
        bind.tvStatus.text = ""
    }

    private fun initActions() {
        bind.btnLoginSendCode.setOnClickListener { sendCode(it as TextView) }
        bind.btnRegisterSendCode.setOnClickListener { sendCode(it as TextView) }
        bind.btnLogin.setOnClickListener { doLogin(isRegister = false) }
        bind.btnRegister.setOnClickListener { doLogin(isRegister = true) }
        bind.btnLoginVoice.setOnClickListener {
            showStatus(getString(R.string.common_coming_soon))
        }
    }

    private fun sendCode(btn: TextView) {
        if (!btn.isEnabled) return
        showStatus(getString(R.string.toast_code_sent))
        btn.isEnabled = false
        object : CountDownTimer(60_000, 1000) {
            override fun onTick(millisUntilFinished: Long) {
                btn.text = getString(R.string.send_code_countdown, (millisUntilFinished / 1000 + 1).toInt())
            }
            override fun onFinish() {
                btn.text = getString(R.string.btn_send_code)
                btn.isEnabled = true
            }
        }.start()
    }

    private fun doLogin(isRegister: Boolean) {
        val phone = (if (isRegister) bind.etRegisterPhone else bind.etLoginPhone).text.toString().trim()
        val code = (if (isRegister) bind.etRegisterCode else bind.etLoginCode).text.toString().trim()
        val name = bind.etRegisterName.text.toString().trim()

        if (!phone.matches(Regex("^1\\d{10}$"))) {
            showStatus(getString(R.string.error_phone_invalid))
            return
        }
        if (code.length < 4) {
            showStatus(getString(R.string.error_code_invalid))
            return
        }
        if (isRegister && name.isEmpty()) {
            showStatus(getString(R.string.error_name_required))
            return
        }

        val finalName = if (isRegister) name else SessionManager.getUserName().ifEmpty { "用户" }
        SessionManager.saveSession(finalName, phone)

        val msg = if (isRegister) {
            val bindMsg = if (bind.cbBindGlasses.isChecked) getString(R.string.register_success_bind_glasses) else ""
            getString(R.string.register_success_fmt, finalName) + bindMsg
        } else {
            getString(R.string.login_success_fmt, finalName)
        }
        bind.root.announceA11y(msg)

        goActivity(MainActivity::class.java)
        finish()
    }

    private fun showStatus(text: String) {
        bind.tvStatus.text = text
    }
}
