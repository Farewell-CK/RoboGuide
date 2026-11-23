package com.seaway.guideassistant.naviindoor

import android.Manifest
import android.content.*
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.*
import android.text.InputType
import android.view.*
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.seaway.guideassistant.naviindoor.core.*
import com.seaway.guideassistant.naviindoor.data.*
import com.seaway.guideassistant.naviindoor.runtime.*
import java.io.File
import java.util.concurrent.Executors

/** Native Android entry; no Flutter engine, method channels or generated AAR. */
open class NativeIndoorActivity: AppCompatActivity() {
    internal lateinit var prefs: NaviPreferences
    internal lateinit var api: NaviApi
    internal lateinit var speech: NaviSpeech
    internal lateinit var sensors: NaviSensors
    internal lateinit var camera: NaviCamera
    internal val handler = Handler(Looper.getMainLooper())
    internal val io = Executors.newSingleThreadExecutor()
    private val cameraWorker = Executors.newSingleThreadExecutor()
    internal val obstacleWorker = Executors.newSingleThreadExecutor()
    internal val gate = SessionGate()
    internal lateinit var body: LinearLayout
    internal lateinit var status: TextView
    internal var profile: Profile? = null
    internal var selectedMap: IndoorMap? = null
    internal var nodes = emptyList<MapNode>()
    internal var paths = emptyList<IndoorPath>()
    internal var mapBitmap: Bitmap? = null
    internal var currentNode: MapNode? = null
    internal var navigation: NavigationSession? = null
    internal var navigationBaseSteps = 0
    internal var draft: RouteDraft? = null
    internal var recording = false
    internal var busy = false
    internal var frameBusy = false
    internal var obstacleBusy = false
    internal var obstacleEnabled = true
    internal var obstacleEpoch = 0
    internal var obstacleLastAt = 0L
    internal var obstacleText: TextView? = null
    internal var headingText: TextView? = null
    internal var guidanceText: TextView? = null
    internal var progressText: TextView? = null
    internal var debugText: TextView? = null
    internal var arrowText: TextView? = null
    internal var resumeLive: (() -> Unit)? = null
    internal val obstacleConfirmation = ObstacleConfirmation()
    internal val diagnostics = ArrayDeque<Map<String, Any?>>()
    private var pausedLive = false
    private var pickCallback: ((Uri) -> Unit)? = null
    private var pickToken = -1
    private var mediaLiveResume: (() -> Unit)? = null
    private var backCallback: androidx.activity.OnBackPressedCallback? = null
    private var cameraPermission: (() -> Unit)? = null
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        sensors.start()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) cameraPermission?.invoke()
        else status.text = "需要相机权限才能定位、录制和识别障碍，请在系统设置中允许后重试。"
        cameraPermission = null
    }
    private val media = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        val callback = pickCallback
        pickCallback = null
        resumeLive = mediaLiveResume; mediaLiveResume = null
        if (uri != null && gate.accepts(pickToken)) callback?.invoke(uri)
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        setTheme(R.style.NaviNativeTheme)
        super.onCreate(savedInstanceState)
        prefs = NaviPreferences(this)
        api = NaviApi(prefs.connection, prefs)
        sensors = NaviSensors(this) { updateHeading() }
        camera = NaviCamera(this, this, cameraWorker)
        speech = NaviSpeech(this) { message("未找到中文语音引擎，字幕仍会显示；可在系统文字转语音设置中安装中文语音。") }
        speech.enabled = prefs.voice
        if (runCatching { prefs.connection.validate() }.isFailure) showSettings() else if (!handleAuth(intent)) showHome()
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); handleAuth(intent) }
    private fun handleAuth(intent: Intent): Boolean {
        val uri = intent.data ?: return false
        if (uri.scheme != "com.example.indoornavigation" || uri.host != "auth") return false
        val values = Uri.parse("https://callback.invalid/?" + (uri.fragment ?: uri.query ?: ""))
        val access = values.getQueryParameter("access_token") ?: return false
        val refresh = values.getQueryParameter("refresh_token") ?: return false
        screen("恢复账号")
        work("正在验证恢复链接", { api.acceptCallback(access, refresh) }) { showNewPassword() }
        return true
    }
    override fun onResume() {
        super.onResume()
        if (::sensors.isInitialized) sensors.start()
        if (pausedLive) { pausedLive = false; resumeLive?.invoke() }
    }
    override fun onPause() {
        if (resumeLive != null) {
            pausedLive = true
            gate.invalidate(); handler.removeCallbacksAndMessages(null); api.cancel(); camera.close()
            recording = false; frameBusy = false; obstacleEpoch++; obstacleConfirmation.clear()
            navigation?.interrupt(); speech.stop()
        }
        sensors.stop()
        super.onPause()
    }
    override fun onDestroy() {
        gate.invalidate(); handler.removeCallbacksAndMessages(null); api.close(); camera.close(); sensors.stop(); speech.close()
        io.shutdownNow(); obstacleWorker.shutdownNow(); cameraWorker.shutdown()
        mapBitmap?.recycle(); mapBitmap = null
        super.onDestroy()
    }
    internal fun screen(title: String, back: (() -> Unit)? = null) {
        gate.invalidate(); handler.removeCallbacksAndMessages(null); api.cancel(); camera.close(); speech.stop()
        navigation = null; recording = false; resumeLive = null
        busy = false; frameBusy = false; obstacleEpoch++; obstacleConfirmation.clear(); obstacleLastAt = 0
        obstacleText = null; headingText = null; guidanceText = null; progressText = null; debugText = null; arrowText = null
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(0xFFF8FAFC.toInt()) }
        val toolbar = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(8), dp(6), dp(12), dp(6)) }
        if (back != null) toolbar.addView(Button(this).apply { text = "返回"; setOnClickListener { back() } }, LinearLayout.LayoutParams(dp(76), dp(48)))
        toolbar.addView(TextView(this).apply { text = title; textSize = 23f; setTextColor(0xFF0F172A.toInt()); setPadding(dp(8), 0, 0, 0) })
        root.addView(toolbar)
        status = TextView(this).apply { textSize = 14f; setTextColor(0xFF475569.toInt()); setPadding(dp(16), dp(6), dp(16), dp(6)); accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        root.addView(status)
        body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(4), dp(16), dp(24)) }
        root.addView(ScrollView(this).apply { isFillViewport = true; addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        backCallback?.remove()
        backCallback = object: androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (back == null) finish() else back() }
        }.also { onBackPressedDispatcher.addCallback(this, it) }
    }
    internal fun <T> work(label: String, action: () -> T, complete: (T) -> Unit) {
        if (busy) return
        busy = true; status.text = label
        val token = gate.generation
        io.execute {
            if (!gate.accepts(token)) return@execute
            val result = runCatching(action)
            runOnUiThread {
                if (!gate.accepts(token) || isDestroyed) return@runOnUiThread
                busy = false
                result.fold({ status.text = ""; complete(it) }, { error ->
                    val text = errorText(error)
                    status.text = text
                    diagnostic("request_error", mapOf("message" to text))
                    speech.speak(text)
                })
            }
        }
    }
    internal fun errorText(error: Throwable): String = when (error) {
        is java.net.SocketTimeoutException -> "请求超时，请检查手机网络与服务器连接后重试。"
        is java.net.ConnectException, is java.net.UnknownHostException -> "无法连接服务器，请检查两个服务地址和当前网络。"
        else -> error.message?.take(240) ?: "操作未完成，请重试并查看诊断记录。"
    }
    internal fun message(text: String) { Toast.makeText(this, text, Toast.LENGTH_LONG).show() }
    internal fun diagnostic(type: String, detail: Map<String, Any?>) {
        diagnostics.addLast(mapOf("type" to type, "time" to java.time.Instant.now().toString()) + detail)
        while (diagnostics.size > 120) diagnostics.removeFirst()
    }
    internal fun copyDiagnostics() {
        val text = "NAVI 原生 Android 测试版\n" + NaviJson.gson.toJson(diagnostics.toList())
        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("NAVI诊断", text))
        message("诊断记录已复制，不含密码或登录令牌。")
    }
    internal fun pick(type: String, result: (Uri) -> Unit) {
        if (busy || frameBusy) return
        mediaLiveResume = resumeLive; resumeLive = null
        pickCallback = result; pickToken = gate.generation; media.launch(type)
    }
    internal fun confirm(title: String, text: String, yes: () -> Unit) {
        AlertDialog.Builder(this).setTitle(title).setMessage(text).setNegativeButton("取消", null)
            .setPositiveButton("确认") { _, _ -> yes() }.show()
    }
    internal fun ask(title: String, initial: String = "", answer: (String) -> Unit) {
        val input = EditText(this).apply { setText(initial); setSingleLine(); setPadding(dp(20), dp(12), dp(20), dp(12)) }
        val dialog = AlertDialog.Builder(this).setTitle(title).setView(input).setNegativeButton("取消", null).setPositiveButton("确定", null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val value = input.text.toString().trim()
            if (value.isEmpty()) input.error = "请填写内容" else { dialog.dismiss(); answer(value) }
        } }
        dialog.show()
    }
    internal fun preview(onReady: () -> Unit): PreviewView {
        val view = PreviewView(this).apply { scaleType = PreviewView.ScaleType.FIT_CENTER; implementationMode = PreviewView.ImplementationMode.COMPATIBLE; contentDescription = "手机后置摄像头实时画面" }
        body.addView(view, LinearLayout.LayoutParams(-1, dp(230)))
        val token = gate.generation
        val bind = { if (gate.accepts(token)) camera.bind(view, onReady) { status.text = "相机启动失败：" + errorText(it) } }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) bind()
        else { cameraPermission = bind; permissions.launch(arrayOf(Manifest.permission.CAMERA, Manifest.permission.ACTIVITY_RECOGNITION)) }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACTIVITY_RECOGNITION) != PackageManager.PERMISSION_GRANTED) {
            body.button("允许计步（可选）") { cameraPermission = null; permissions.launch(arrayOf(Manifest.permission.ACTIVITY_RECOGNITION)) }
        }
        return view
    }
    internal fun updateHeading() {
        headingText?.text = "朝向：" + (sensors.heading?.let { "%.0f°".format(it) } ?: "等待罗盘") + "   步行信号：" + sensors.steps + if (sensors.stepsAvailable) "" else "（可手动到位确认）"
        val session = navigation ?: return
        val delta = sensors.heading?.let { relativeHeading(it, session.path.waypoints[minOf(session.target, session.path.waypoints.lastIndex)].heading) }
        arrowText?.rotation = delta?.toFloat() ?: 0f
        arrowText?.contentDescription = delta?.let { if (it >= 0) "目标方向在右侧约" + it.toInt() + "度" else "目标方向在左侧约" + (-it).toInt() + "度" } ?: "等待朝向"
        if (session.phase == NavigationPhase.orienting) renderNavigation(session.orient(sensors.heading))
    }
    internal fun showSettings() {
        screen("NAVI 连接设置") { if (runCatching { prefs.connection.validate() }.isSuccess) showHome() else finish() }
        body.label("原生 Android 版沿用现有视觉服务和 Supabase 数据。")
        val saved = prefs.connection
        val vision = body.field("DINOv2／避障服务地址", saved.vision, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        val database = body.field("Supabase 项目或代理地址", saved.database, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        val key = body.field("Supabase 客户端公开密钥", saved.publicKey, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        fun values() = Connection(vision.text.toString().trim(), database.text.toString().trim(), key.text.toString().trim()).also { it.validate() }
        body.button("测试两个服务的连接") {
            val config = values()
            work("检查视觉服务和数据库接口…", { NaviApi(config, prefs).use { it.checkConnection() } }) { status.text = "两个服务均可访问。" }
        }
        body.button("保存并继续") {
            val config = values()
            api.close(); prefs.connection = config; api = NaviApi(config, prefs); showHome()
        }
    }
    internal fun showLogin() {
        screen("登录 NAVI") { finish() }
        body.label("登录原来的 NAVI 账号即可使用已有地图。")
        val email = body.field("邮箱", inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS)
        val password = body.field("密码", inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        body.button("登录") {
            val mail = email.text.toString().trim(); val pass = password.text.toString()
            require(mail.contains('@') && pass.isNotEmpty()) { "请填写邮箱和密码" }
            work("正在登录…", { api.signIn(mail, pass) }) { showHome() }
        }
        body.button("注册普通账号") {
            val mail = email.text.toString().trim(); val pass = password.text.toString()
            require(mail.contains('@') && pass.length >= 6) { "请填写邮箱和至少6位密码" }
            work("正在注册…", { api.signUp(mail, pass) }) { loggedIn ->
                if (loggedIn) showHome() else status.text = "注册请求已提交。若项目开启邮箱验证，请完成验证后登录；管理员权限由已有管理员授权。"
            }
        }
        body.button("忘记密码") {
            val mail = email.text.toString().trim(); require(mail.contains('@')) { "请先填写邮箱" }
            work("正在发送恢复邮件…", { api.recover(mail) }) { status.text = "恢复请求已提交，请查看邮箱并在本机打开恢复链接。" }
        }
        body.button("连接设置") { showSettings() }
        body.button("复制诊断记录") { copyDiagnostics() }
    }
    private fun showNewPassword() {
        screen("设置新密码") { showHome() }
        val password = body.field("新密码", inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        body.button("更新密码") {
            val value = password.text.toString(); require(value.length >= 6) { "密码至少6位" }
            work("正在更新密码…", { api.updatePassword(value) }) { showHome() }
        }
    }
    internal fun showHome() {
        if (runCatching { prefs.connection.validate() }.isFailure) { showSettings(); return }
        if (prefs.session == null) { showLogin(); return }
        screen("NAVI 室内导航") { finish() }
        work("读取账号权限…", { api.profile() }) { user ->
            profile = user
            body.label(user.email + if (user.admin) " · 管理员" else " · 用户")
            body.label("选择地图后定位，再选择目的地。", 20f)
            body.button("地图与导航") { showMaps() }
            body.button("本地路线草稿") { showDrafts() }
            if (user.admin) body.button("用户与组织管理") { showUserManagement() }
            body.addView(CheckBox(this).apply { text = "中文语音提示（字幕始终显示）"; isChecked = prefs.voice
                setOnCheckedChangeListener { _, checked -> prefs.voice = checked; speech.enabled = checked; if (!checked) speech.stop() } })
            body.button("连接设置") { showSettings() }
            body.button("复制诊断记录") { copyDiagnostics() }
            body.button("退出账号") { work("正在退出…", { api.signOut() }) { profile = null; currentNode = null; showLogin() } }
        }
        body.button("连接设置／重试") { showSettings() }
    }
    internal fun dp(value: Int) = (resources.displayMetrics.density * value).toInt()
    internal val draftRoot get() = File(filesDir, "navi-native-drafts").apply { mkdirs() }
}

internal fun LinearLayout.label(value: String, size: Float = 17f): TextView = TextView(context).apply {
    text = value; textSize = size; setTextColor(0xFF0F172A.toInt()); setPadding(0, 12, 0, 12)
    setTextIsSelectable(true)
}.also { addView(it, LinearLayout.LayoutParams(-1, -2)) }
internal fun LinearLayout.button(value: String, click: () -> Unit): Button = Button(context).apply {
    text = value; isAllCaps = false; minHeight = (52 * resources.displayMetrics.density).toInt()
    setOnClickListener { runCatching(click).onFailure { Toast.makeText(context, it.message ?: "操作未完成", Toast.LENGTH_LONG).show() } }
}.also { addView(it, LinearLayout.LayoutParams(-1, -2)) }
internal fun LinearLayout.field(title: String, value: String = "", inputType: Int = InputType.TYPE_CLASS_TEXT): EditText {
    label(title, 15f)
    return EditText(context).apply { hint = title; setText(value); this.inputType = inputType; setSingleLine(); contentDescription = title }.also { addView(it, LinearLayout.LayoutParams(-1, -2)) }
}
