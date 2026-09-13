package com.seaway.guideassistant.api

import com.seaway.guideassistant.base.Constant
import com.seaway.guideassistant.bean.IntentRequest
import com.seaway.guideassistant.bean.IntentResponse
import com.seaway.guideassistant.http.BaseResponse
import com.seaway.guideassistant.http.im
import com.seaway.guideassistant.llm.LocalIntentClassifier
import io.reactivex.Observable
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.POST

interface IntentApi {
    @POST("v1/intent/recognize")
    fun recognize(@Header("Authorization") auth: String, @Body body: IntentRequest): Observable<BaseResponse<IntentResponse>>
}

class IntentImpl : IntentApi, KoinComponent {
    private val api: IntentApi by inject()

    override fun recognize(auth: String, body: IntentRequest): Observable<BaseResponse<IntentResponse>> = api.recognize(auth, body).im()

    /**
     * 大模型意图识别：接口地址/密钥未配置（预留）时直接走本地关键词兜底，网络失败时同样兜底，不向上抛错
     */
    fun recognize(text: String): Observable<IntentResponse> {
        if (Constant.LLM_API_KEY.isBlank()) {
            return Observable.fromCallable { LocalIntentClassifier.classify(text) }
        }
        return recognize("Bearer ${Constant.LLM_API_KEY}", IntentRequest(text))
            .map { it.result ?: LocalIntentClassifier.classify(text) }
            .onErrorReturn { LocalIntentClassifier.classify(text) }
    }
}
