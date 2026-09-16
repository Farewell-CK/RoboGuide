package com.seaway.guideassistant.base

object Constant {
    const val BASE_URL: String = "http://v.juhe.cn/"
    const val BASE_PHONE_URL: String = "http://apis.juhe.cn/"

    //ASR语音识别服务（WebSocket协议，详见外部ASR Service API文档）
    const val ASR_WS_URL: String = "wss://aitoys.seawayos.com/asr/v1/"
    const val ASR_AUTH_TOKEN: String = "906991e73318694909fd99dc512e75ef75b7dd0a2dbe81872ced3274410ef8e1"

    //设备状态/画面/导航上报服务（WebSocket协议，详见设备监控 watch 服务文档）
    const val DMWS_URL: String = "wss://watch.seawayos.com/dmws"
    const val DMWS_TOKEN: String = "f4h8j2q7b5n9c1v3" //需要真实设备鉴权Token填入此处，缺省时连接会被服务端拒绝

    //大模型意图识别接口，预留：暂无真实地址和key，留空时走本地关键词兜底识别
    const val LLM_API_BASE_URL: String = ""
    const val LLM_API_KEY: String = "sk-ws-H.PDIEILX.7j27.MEQCIGvUmUXDT6jdt74-PIW34bgWJOd4xQO8grbER0ySzgxhAiBqp1TXT4PLNLyg4DjRjnupmQtg4ai3Gt2inDSRFWtA7A"
    val timeoutMillis: Int = 20_000
    const val AMAP_KEY:String = "1d9ca20ae160111eaff0426505e81997"
}