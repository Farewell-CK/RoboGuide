package com.seaway.guideassistant.naviindoor

import android.Manifest
import android.content.*
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.*
import android.text.InputType
import android.text.Editable
import android.text.TextWatcher
import android.text.TextUtils
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
    internal lateinit var toolbarTitle: TextView
    private lateinit var toolbarActions: LinearLayout
    private var cameraHost: FrameLayout? = null
    internal var cameraOverlay: FrameLayout? = null
    internal var routeProgress: ProgressBar? = null
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
    internal fun screen(title: String, colour: Int = NaviStyle.BLACK, immersive: Boolean = false, back: (() -> Unit)? = null) {
        gate.invalidate(); handler.removeCallbacksAndMessages(null); api.cancel(); camera.close(); speech.stop()
        navigation = null; recording = false; resumeLive = null
        busy = false; frameBusy = false; obstacleEpoch++; obstacleConfirmation.clear(); obstacleLastAt = 0
        obstacleText = null; headingText = null; guidanceText = null; progressText = null; debugText = null; arrowText = null
        routeProgress = null; cameraHost = null; cameraOverlay = null
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.statusBarColor = if (immersive) Color.BLACK else colour
        window.navigationBarColor = if (immersive) Color.BLACK else Color.WHITE
        window.decorView.systemUiVisibility = if (immersive) 0 else View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        val root = FrameLayout(this).apply { setBackgroundColor(if (immersive) Color.BLACK else Color.WHITE) }
        val toolbar = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setBackgroundColor(if (immersive) 0x99000000.toInt() else colour); minimumHeight = dp(56) }
        if (back != null) toolbar.addView(ImageButton(this).apply {
            setImageResource(R.drawable.ic_navi_back); contentDescription = "返回"; tooltipText = "返回"; setBackgroundColor(Color.TRANSPARENT); safeClick(back)
        }, LinearLayout.LayoutParams(dp(48), dp(56))) else toolbar.addView(View(this), LinearLayout.LayoutParams(dp(48), dp(56)))
        toolbarTitle = TextView(this).apply { text = title; textSize = 21f; setTextColor(Color.WHITE); setTypeface(typeface, Typeface.BOLD); gravity = Gravity.CENTER; maxLines = 1; ellipsize = TextUtils.TruncateAt.END }
        toolbar.addView(toolbarTitle, LinearLayout.LayoutParams(0, -2, 1f))
        toolbarActions = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; minimumWidth = dp(48) }
        toolbar.addView(toolbarActions, LinearLayout.LayoutParams(-2, dp(56)))
        val statusRow = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; visibility = View.GONE; setPadding(dp(16), dp(8), dp(16), dp(8)); setBackgroundColor(if (immersive) 0xDD000000.toInt() else 0xFFF5F5F5.toInt()) }
        status = TextView(this).apply { textSize = 14f; setTextColor(if (immersive) Color.WHITE else 0xFF424242.toInt()); accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        statusRow.addView(status, LinearLayout.LayoutParams(-1, -2))
        status.addTextChangedListener(object: TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { statusRow.visibility = if (s.isNullOrBlank()) View.GONE else View.VISIBLE }
            override fun afterTextChanged(s: Editable?) = Unit
        })
        body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(if (immersive) 16 else 24), dp(if (immersive) 12 else 20), dp(if (immersive) 16 else 24), dp(24))
            if (immersive) { tag = NaviStyle.DARK_PANEL; background = NaviStyle.shape(this@NativeIndoorActivity, 0xED000000.toInt(), 20) }
        }
        if (immersive) {
            cameraHost = FrameLayout(this).also { layer ->
                layer.addView(TextView(this).apply { text = "正在准备相机…"; textSize = 18f; setTextColor(Color.WHITE); gravity = Gravity.CENTER }, FrameLayout.LayoutParams(-1, -1))
                root.addView(layer, FrameLayout.LayoutParams(-1, -1))
            }
            cameraOverlay = FrameLayout(this).also { root.addView(it, FrameLayout.LayoutParams(-1, -1)) }
            root.addView(toolbar, FrameLayout.LayoutParams(-1, dp(56), Gravity.TOP))
            root.addView(statusRow, FrameLayout.LayoutParams(-1, -2, Gravity.TOP).apply { topMargin = dp(56) })
            root.addView(NaviPanelScroll(this).apply { addView(body); isFillViewport = false }, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        } else {
            val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            column.addView(toolbar, LinearLayout.LayoutParams(-1, dp(56)))
            column.addView(statusRow, LinearLayout.LayoutParams(-1, -2))
            column.addView(ScrollView(this).apply { isFillViewport = true; addView(body) }, LinearLayout.LayoutParams(-1, 0, 1f))
            root.addView(column, FrameLayout.LayoutParams(-1, -1))
        }
        setContentView(root)
        backCallback?.remove()
        backCallback = object: androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (back == null) finish() else back() }
        }.also { onBackPressedDispatcher.addCallback(this, it) }
    }
    internal fun <T> work(label: String, action: () -> T, failed: ((Throwable) -> Unit)? = null, complete: (T) -> Unit) {
        if (busy) return
        busy = true; status.text = label
        val token = gate.generation
        val started = SystemClock.elapsedRealtime()
        io.execute {
            if (!gate.accepts(token)) return@execute
            val result = runCatching(action)
            runOnUiThread {
                if (!gate.accepts(token) || isDestroyed) return@runOnUiThread
                busy = false
                diagnostic("page_load", mapOf("stage" to label, "elapsed_ms" to SystemClock.elapsedRealtime() - started, "success" to result.isSuccess))
                result.fold({ status.text = ""; complete(it) }, { error ->
                    val text = errorText(error)
                    status.text = text
                    diagnostic("request_error", mapOf("stage" to label, "message" to text))
                    failed?.invoke(error)
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
    internal fun toolbarAction(title: String, icon: Int, action: () -> Unit) {
        toolbarActions.addView(ImageButton(this).apply {
            setImageResource(icon); contentDescription = title; tooltipText = title; setBackgroundColor(Color.TRANSPARENT); safeClick(action)
        }, LinearLayout.LayoutParams(dp(48), dp(56)))
    }
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
        val view = PreviewView(this).apply { scaleType = if (cameraHost == null) PreviewView.ScaleType.FIT_CENTER else PreviewView.ScaleType.FILL_CENTER; implementationMode = PreviewView.ImplementationMode.COMPATIBLE; contentDescription = "手机后置摄像头实时画面" }
        val host = cameraHost
        if (host == null) body.addView(view, LinearLayout.LayoutParams(-1, dp(260)))
        else { host.removeAllViews(); host.addView(view, FrameLayout.LayoutParams(-1, -1)) }
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
        body.label("配置导航视觉服务与账号数据服务。")
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
        body.button("管理员注册（需授权）") {
            val mail = email.text.toString().trim(); val pass = password.text.toString()
            require(mail.contains('@') && pass.length >= 6) { "请填写邮箱和至少6位密码" }
            AlertDialog.Builder(this).setTitle("管理员注册")
                .setMessage("先创建账号，再由项目管理员核验并授权。账号在授权之前仅拥有普通用户权限。")
                .setNegativeButton("取消", null)
                .setPositiveButton("创建账号") { _, _ ->
                    work("正在创建账号…", { api.signUp(mail, pass) }) { loggedIn ->
                        val notice = if (loggedIn) "账号已创建，请联系项目管理员授权。" else
                            "注册请求已提交。请按邮箱提示完成注册，再联系项目管理员授权。"
                        if (loggedIn) { showHome(); message(notice) } else status.text = notice
                    }
                }.show()
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
        screen("NAVI") { finish() }
        toolbarAction("连接设置", R.drawable.ic_navi_settings) { showSettings() }
        work("读取账号权限…", { api.profile() }) { user ->
            profile = user
            renderHome(user)
        }
    }
    internal fun dp(value: Int) = (resources.displayMetrics.density * value).toInt()
    internal val draftRoot get() = File(filesDir, "navi-native-drafts").apply { mkdirs() }
}
