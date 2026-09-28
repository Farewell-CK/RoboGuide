package com.seaway.guideassistant.utils

import android.view.View

/**
 * 无障碍相关扩展方法：统一状态变化区域的TalkBack播报方式
 */
fun View.setLiveRegionPolite() {
    accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
}

fun View.setLiveRegionAssertive() {
    accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_ASSERTIVE
}

/**
 * 主动向TalkBack播报一段文本，用于toast类的一次性提示（替代不支持无障碍的ToastUtil）
 */
fun View.announceA11y(text: CharSequence) {
    announceForAccessibility(text)
}
