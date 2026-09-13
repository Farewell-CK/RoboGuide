package com.seaway.guideassistant.http

import com.seaway.guideassistant.api.IntentImpl
import com.seaway.guideassistant.api.MobileImpl
import com.seaway.guideassistant.api.WeatherImpl
import org.koin.core.component.KoinComponent
import org.koin.core.component.get

/**
 * 网络数据提供者
 */
class DataProvider :KoinComponent {
    val weather: WeatherImpl = get()
    val mobile: MobileImpl = get()
    val intent: IntentImpl = get()
}