package com.seaway.guideassistant.base

import android.os.Bundle
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.view.updatePadding
import androidx.viewbinding.ViewBinding
import com.dylanc.viewbinding.inflateBindingWithGeneric
import com.seaway.smallutils.text.NavigationBar
import com.seaway.guideassistant.R


abstract class BaseBindActivity<VB:ViewBinding>: BaseActivity() {
    lateinit var bind: VB
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        bind = inflateBindingWithGeneric(layoutInflater)
        setContentView(bind.root)
        //固定深色主题，状态栏图标使用浅色；内容区改为自行处理系统栏边距，避免被状态栏/导航栏遮挡
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, bind.root).isAppearanceLightStatusBars = false
        ViewCompat.setOnApplyWindowInsetsListener(bind.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            //底部边距取系统栏与键盘中的较大值，键盘弹出时把内容（如输入框）顶上去，避免被遮挡
            view.updatePadding(top = bars.top, bottom = maxOf(bars.bottom, ime.bottom))
            insets
        }
        //导航栏设置：使用深色高对比配色，状态栏颜色统一由主题控制，不再走StatusBar工具类
        val bar = NavigationBar(this)
        bar.setBackgroundColor(ContextCompat.getColor(this, R.color.color_surface), false)
        bar.titleView?.setTextColor(ContextCompat.getColor(this, R.color.color_text))
        bar.backImageView?.setImageResource(R.mipmap.ic_back)
        bar.backImageView?.contentDescription = getString(R.string.cd_back)
        bar.backImageView?.setOnClickListener{finish()}
        onCreate(savedInstanceState,bar)

    }
    abstract fun onCreate(savedInstanceState: Bundle?,bar:NavigationBar)

}