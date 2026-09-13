package com.seaway.guideassistant.module

import android.app.Activity
import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.seaway.guideassistant.api.IntentApi
import com.seaway.guideassistant.api.IntentImpl
import com.seaway.guideassistant.api.MobileApi
import com.seaway.guideassistant.api.MobileImpl
import com.seaway.guideassistant.api.WeatherApi
import com.seaway.guideassistant.api.WeatherImpl
import com.seaway.guideassistant.base.Constant
import com.seaway.guideassistant.http.DataProvider
import com.seaway.guideassistant.http.HandleErrorInterceptor
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import org.koin.core.parameter.parametersOf
import org.koin.core.qualifier.named
import org.koin.dsl.module
import retrofit2.Retrofit
import retrofit2.adapter.rxjava2.RxJava2CallAdapterFactory
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * 网络请求依赖注入module
 */

val httpModule = module {
    //网络数据json格式化
    val gson: Gson? = GsonBuilder()
        .setDateFormat("yyyy-MM-dd HH:mm:ss")
        .serializeNulls()
        .create()

    //网络请求okhttp客户端
    val okHttpClientBuilder: OkHttpClient.Builder =OkHttpClient.Builder()
    okHttpClientBuilder.addInterceptor(HandleErrorInterceptor())//日志打印拦截器


    //公共头部拦截器

    okHttpClientBuilder.addInterceptor(Interceptor{
    val request: Request = it.request()
        .newBuilder()
        .addHeader("Content-Type", "application/json;charset=UTF-8")
//        .addHeader("token", SpUtil.getToken()?:"")
        .build()
        it.proceed(request)
    })

    val okHttpClient = okHttpClientBuilder.build()

    single { okHttpClient }
    single { gson!! }

    // 专供 WebSocket（如语音 ASR）使用的客户端：不能复用上面带 HandleErrorInterceptor 的
    // okHttpClient——该拦截器会提前读取/消费响应体（source.request(Long.MAX_VALUE)），这会
    // 打断 OkHttp 对 WebSocket 升级响应的握手移交，导致连接建立后几乎立刻回调 onFailure。
    // 同时禁用 readTimeout（WebSocket 连接期间可能长时间无数据）并开启心跳保活。
    single(named("webSocket")) {
        OkHttpClient.Builder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }

    //单例，加载圈圈
    single {(context: Activity) ->LoadDialog(context)
//        XPopup.Builder(MyApplication.instance.applicationContext).asLoading().setTitle("加载中...")
    }
    //单例retrofit,需要单独定义主机地址
    single (named("hasUrl")){ (url:String?)->
        Retrofit.Builder()
            .baseUrl(url?: Constant.BASE_URL)
            .client(okHttpClient)
            .addCallAdapterFactory(RxJava2CallAdapterFactory.create())// 支持RxJava2
            .addConverterFactory(GsonConverterFactory.create(gson))
            .build()
    }
    single (named("siteUrl")){ (url:String?)->
        Retrofit.Builder()
            .baseUrl(url?: Constant.BASE_URL)
            .client(okHttpClient)
            .addCallAdapterFactory(RxJava2CallAdapterFactory.create())// 支持RxJava2
            .addConverterFactory(GsonConverterFactory.create(gson))
            .build()
    }
    //单例retrofit
    single {
         Retrofit.Builder()
            .baseUrl(Constant.BASE_URL)
            .client(okHttpClient)
            .addCallAdapterFactory(RxJava2CallAdapterFactory.create())// 支持RxJava2
            .addConverterFactory(GsonConverterFactory.create(gson))
            .build()
    }
    //网络数据提供者

    single {DataProvider()}
    single { WeatherImpl() }
    single { MobileImpl() }
    single { IntentImpl() }
    single {get<Retrofit>().create(WeatherApi::class.java)}
    single {get<Retrofit>(named("hasUrl")){parametersOf(Constant.BASE_PHONE_URL)}.create(MobileApi::class.java)}
    //大模型意图识别接口：地址预留，未配置时IntentImpl内部走本地兜底，不会真正发起该请求
    single {get<Retrofit>(named("hasUrl")){parametersOf(Constant.LLM_API_BASE_URL.ifBlank { Constant.BASE_URL })}.create(IntentApi::class.java)}

}




