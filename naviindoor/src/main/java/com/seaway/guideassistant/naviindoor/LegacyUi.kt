package com.seaway.guideassistant.naviindoor

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.appcompat.widget.AppCompatButton
import androidx.appcompat.widget.SwitchCompat
import com.seaway.guideassistant.naviindoor.data.Profile

/** The colours, large touch targets and hierarchy of the existing Flutter NAVI. */
internal object NaviStyle {
    const val BLACK = Color.BLACK
    const val WHITE = Color.WHITE
    const val GREEN = 0xFF43A047.toInt()
    const val ORANGE = 0xFFFB8C00.toInt()
    const val BLUE = 0xFF1E88E5.toInt()
    const val TEAL = 0xFF009688.toInt()
    const val RED = 0xFFC62828.toInt()
    const val DARK_PANEL = "navi-dark-panel"
    fun dp(context: Context, value: Int) = (context.resources.displayMetrics.density * value).toInt()
    fun shape(context: Context, colour: Int, radius: Int = 12, border: Int? = null) = GradientDrawable().apply {
        setColor(colour); cornerRadius = dp(context, radius).toFloat()
        if (border != null) setStroke(dp(context, 1), border)
    }
}

internal fun View.safeClick(action: () -> Unit) {
    setOnClickListener { runCatching(action).onFailure { Toast.makeText(context, it.message ?: "操作未完成", Toast.LENGTH_LONG).show() } }
}

internal fun LinearLayout.label(value: String, size: Float = 17f): TextView = TextView(context).apply {
    text = value; textSize = size
    setTextColor(if (this@label.tag == NaviStyle.DARK_PANEL) Color.WHITE else 0xFF212121.toInt())
    setPadding(0, NaviStyle.dp(context, 6), 0, NaviStyle.dp(context, 6))
    if (size >= 20f) setTypeface(typeface, Typeface.BOLD)
    setTextIsSelectable(true)
}.also { addView(it, LinearLayout.LayoutParams(-1, -2)) }

internal fun LinearLayout.button(value: String, colour: Int = NaviStyle.BLUE, outlined: Boolean = false, click: () -> Unit): Button {
    val dark = tag == NaviStyle.DARK_PANEL
    return AppCompatButton(context).apply {
        text = value; textSize = 16f; isAllCaps = false
        minHeight = NaviStyle.dp(context, 52); minimumHeight = minHeight
        setPadding(NaviStyle.dp(context, 16), NaviStyle.dp(context, 12), NaviStyle.dp(context, 16), NaviStyle.dp(context, 12))
        val fill = if (outlined) { if (dark) 0xFF263238.toInt() else Color.WHITE } else colour
        setTextColor(if (outlined && !dark) colour else Color.WHITE)
        backgroundTintList = null
        background = RippleDrawable(ColorStateList.valueOf(0x33000000), NaviStyle.shape(context, fill, 10, if (outlined) colour else null), null)
        stateListAnimator = null
        safeClick(click)
    }.also { addView(it, LinearLayout.LayoutParams(-1, -2).apply { topMargin = NaviStyle.dp(context, 8); bottomMargin = NaviStyle.dp(context, 4) }) }
}

internal fun LinearLayout.field(title: String, value: String = "", inputType: Int = InputType.TYPE_CLASS_TEXT): EditText {
    label(title, 15f)
    return EditText(context).apply {
        hint = title; setText(value); this.inputType = inputType; setSingleLine(); contentDescription = title
        textSize = 16f; setTextColor(Color.BLACK); setHintTextColor(0xFF757575.toInt())
        setPadding(NaviStyle.dp(context, 14), NaviStyle.dp(context, 12), NaviStyle.dp(context, 14), NaviStyle.dp(context, 12))
        background = NaviStyle.shape(context, Color.WHITE, 10, 0xFFBDBDBD.toInt())
    }.also { addView(it, LinearLayout.LayoutParams(-1, NaviStyle.dp(context, 54)).apply { bottomMargin = NaviStyle.dp(context, 10) }) }
}

internal fun LinearLayout.card(): LinearLayout = LinearLayout(context).apply {
    orientation = LinearLayout.VERTICAL; tag = this@card.tag
    setPadding(NaviStyle.dp(context, 16), NaviStyle.dp(context, 12), NaviStyle.dp(context, 16), NaviStyle.dp(context, 12))
    background = NaviStyle.shape(context, if (tag == NaviStyle.DARK_PANEL) 0xFF263238.toInt() else Color.WHITE, 12, 0xFFE0E0E0.toInt())
}.also { addView(it, LinearLayout.LayoutParams(-1, -2).apply { topMargin = NaviStyle.dp(context, 12); bottomMargin = NaviStyle.dp(context, 4) }) }

internal fun LinearLayout.actionCard(title: String, subtitle: String, icon: Int, colour: Int, action: () -> Unit) {
    val row = LinearLayout(context).apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(NaviStyle.dp(context, 20), NaviStyle.dp(context, 16), NaviStyle.dp(context, 16), NaviStyle.dp(context, 16))
        background = RippleDrawable(ColorStateList.valueOf(0x33000000), NaviStyle.shape(context, colour, 16, Color.BLACK), null)
        isFocusable = true; safeClick(action)
        contentDescription = title + "，" + subtitle
    }
    row.addView(ImageView(context).apply { setImageResource(icon); importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO }, LinearLayout.LayoutParams(NaviStyle.dp(context, 44), NaviStyle.dp(context, 44)))
    val copy = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(NaviStyle.dp(context, 18), 0, NaviStyle.dp(context, 8), 0) }
    copy.addView(TextView(context).apply { text = title; textSize = 21f; setTextColor(Color.WHITE); setTypeface(typeface, Typeface.BOLD) })
    copy.addView(TextView(context).apply { text = subtitle; textSize = 14f; setTextColor(0xEEFFFFFF.toInt()); setPadding(0, NaviStyle.dp(context, 6), 0, 0) })
    row.addView(copy, LinearLayout.LayoutParams(0, -2, 1f))
    row.addView(TextView(context).apply { text = "›"; textSize = 32f; setTextColor(Color.WHITE); importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO })
    addView(row, LinearLayout.LayoutParams(-1, -2).apply { height = maxOf(NaviStyle.dp(context, 98), NaviStyle.dp(context, (82 * resources.configuration.fontScale).toInt())); bottomMargin = NaviStyle.dp(context, 20) })
}

internal fun NativeIndoorActivity.renderHome(user: Profile) {
    toolbarTitle.text = if (user.admin) "管理员首页" else "用户首页"
    val hero = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(26)) }
    hero.addView(ImageView(this).apply { setImageResource(R.drawable.navi_logo); contentDescription = "NAVI 图标" }, LinearLayout.LayoutParams(dp(100), dp(100)))
    hero.label("欢迎使用 NAVI", 22f).apply { gravity = Gravity.CENTER }
    hero.label("请选择操作", 15f).apply { gravity = Gravity.CENTER; setTextColor(0xFF616161.toInt()) }
    body.addView(hero, LinearLayout.LayoutParams(-1, -2))
    body.actionCard("导航", "开始定位与导航", R.drawable.ic_navi_navigation, NaviStyle.GREEN) { showMaps(true) }
    if (user.admin) body.actionCard("地图管理", "管理地图、节点和路线", R.drawable.ic_navi_map, NaviStyle.ORANGE) { showMaps() }
    body.actionCard("个人中心", "账号与连接设置", R.drawable.ic_navi_person, NaviStyle.BLUE) { showProfile() }
}

internal fun NativeIndoorActivity.showProfile() {
    val user = profile ?: return showHome()
    screen("个人中心") { showHome() }
    val account = body.card()
    account.label(user.email, 20f)
    account.label(if (user.admin) "管理员" else "普通用户", 15f)
    body.addView(SwitchCompat(this).apply {
        text = "中文语音提示"; textSize = 17f; setTextColor(Color.BLACK); minHeight = dp(64)
        isChecked = prefs.voice
        setOnCheckedChangeListener { _, checked -> prefs.voice = checked; speech.enabled = checked; if (!checked) speech.stop() }
    }, LinearLayout.LayoutParams(-1, -2))
    body.label("导航字幕始终显示，语音可在此开启或关闭。", 14f)
    body.button("连接设置") { showSettings() }
    body.button("本地路线草稿", NaviStyle.ORANGE) { showDrafts() }
    if (user.admin) body.button("用户与组织管理", NaviStyle.GREEN) { showUserManagement() }
    body.button("复制诊断记录", NaviStyle.TEAL, outlined = true) { copyDiagnostics() }
    body.button("退出账号", NaviStyle.RED, outlined = true) {
        confirm("退出账号", "退出当前 NAVI 账号？") {
            work("正在退出…", { api.signOut() }) { profile = null; currentNode = null; showLogin() }
        }
    }
}

internal class NaviPanelScroll(context: Context): ScrollView(context) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val available = MeasureSpec.getSize(heightMeasureSpec)
        val limit = (resources.displayMetrics.heightPixels * .57f).toInt()
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(if (available == 0) limit else minOf(available, limit), MeasureSpec.AT_MOST))
    }
}
