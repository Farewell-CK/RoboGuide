package com.seaway.guideassistant

import android.os.Bundle
import androidx.core.content.ContextCompat
import com.lsxiao.apollo.core.annotations.Receive
import com.seaway.smallutils.text.NavigationBar
import com.seaway.guideassistant.auth.LoginActivity
import com.seaway.guideassistant.base.BaseBindActivity
import com.seaway.guideassistant.ble.GlassesManager
import com.seaway.guideassistant.databinding.ActivityMainBinding
import com.seaway.guideassistant.fragment.AgentFragment
import com.seaway.guideassistant.fragment.DevicesFragment
import com.seaway.guideassistant.fragment.HomeFragment
import com.seaway.guideassistant.fragment.NavigateFragment
import com.seaway.guideassistant.fragment.ObstacleFragment
import com.seaway.guideassistant.utils.ApolloEvents
import com.seaway.guideassistant.utils.BottomNavUtils
import com.seaway.guideassistant.utils.SessionManager
import com.seaway.guideassistant.utils.TabNavBottomBean

class MainActivity : BaseBindActivity<ActivityMainBinding>() {

    override fun onCreate(savedInstanceState: Bundle?, bar: NavigationBar) {
        bar.hide()
        if (!SessionManager.isLoggedIn()) {
            goActivity(LoginActivity::class.java)
            finish()
            return
        }
        initView()
        GlassesManager.requestPermissionsAndScan(this)
    }

    private fun initView() {
        val colorSelect = ContextCompat.getColor(this, R.color.color_primary)
        val colorUnSelect = ContextCompat.getColor(this, R.color.color_text_muted)
        val tabs = listOf(
            TabNavBottomBean(getString(R.string.tab_home), R.drawable.ic_tab_home_sel, R.drawable.ic_tab_home_unsel, colorSelect, colorUnSelect),
            TabNavBottomBean(getString(R.string.tab_navigate), R.drawable.ic_tab_navigate_sel, R.drawable.ic_tab_navigate_unsel, colorSelect, colorUnSelect),
            TabNavBottomBean(getString(R.string.tab_agent), R.drawable.ic_tab_agent_sel, R.drawable.ic_tab_agent_unsel, colorSelect, colorUnSelect),
//            TabNavBottomBean(getString(R.string.tab_obstacle), R.drawable.ic_tab_obstacle_sel, R.drawable.ic_tab_obstacle_unsel, colorSelect, colorUnSelect),
            TabNavBottomBean(getString(R.string.tab_devices), R.drawable.ic_tab_devices_sel, R.drawable.ic_tab_devices_unsel, colorSelect, colorUnSelect),
        )
        val fragments = listOf(HomeFragment(), NavigateFragment(), AgentFragment(), ObstacleFragment(), DevicesFragment())
        bind.viewPager.isUserInputEnabled = false
        BottomNavUtils.initTabNavi(this, bind.tabLayout, bind.viewPager, tabs, fragments)
    }

    @Receive(ApolloEvents.SWITCH_TO_NAVIGATE_TAB)
    fun onSwitchToNavigateTab() {
        bind.tabLayout.getTabAt(1)?.select()
    }
}
