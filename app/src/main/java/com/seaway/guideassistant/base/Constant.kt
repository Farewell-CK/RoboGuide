package com.seaway.guideassistant.base

import com.seaway.guideassistant.BuildConfig

object Constant {
    const val BASE_URL: String = "http://v.juhe.cn/"
    const val BASE_PHONE_URL: String = "http://apis.juhe.cn/"

    //ASR语音识别服务（WebSocket协议，详见外部ASR Service API文档）
    const val ASR_WS_URL: String = "wss://aitoys.seawayos.com/asr/v1/"
    val ASR_AUTH_TOKEN: String = BuildConfig.ASR_AUTH_TOKEN

    //设备状态/画面/导航上报服务（WebSocket协议，详见设备监控 watch 服务文档）
    const val DMWS_URL: String = "wss://watch.seawayos.com/dmws"
    const val DMWS_TOKEN: String = BuildConfig.DMWS_TOKEN

    //大模型意图识别接口
    const val LLM_API_BASE_URL: String = ""
    const val LLM_API_KEY: String = BuildConfig.LLM_API_KEY
    val timeoutMillis: Int = 20_000
    const val AMAP_KEY:String = BuildConfig.AMAP_KEY
}
