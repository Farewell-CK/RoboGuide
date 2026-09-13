package com.seaway.guideassistant.api

import com.seaway.guideassistant.bean.PhoneRespone
import com.seaway.guideassistant.http.BaseResponse
import com.seaway.guideassistant.http.im
import io.reactivex.Observable
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import retrofit2.http.GET
import retrofit2.http.Query

interface MobileApi {
    @GET("mobile/get")
    fun mobileGet(@Query("phone")phone: String, @Query("key")key:String="c95c37113391b9fff7854ce0eafe496d"): Observable<BaseResponse<PhoneRespone>>
}

class MobileImpl :MobileApi , KoinComponent {
    private val api: MobileApi by inject()
    override fun mobileGet(mobile: String, key: String): Observable<BaseResponse<PhoneRespone>>
            = api.mobileGet(mobile).im()

}