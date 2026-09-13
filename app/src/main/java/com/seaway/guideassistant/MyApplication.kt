package com.seaway.guideassistant

import android.app.Application
import android.content.Context
import androidx.multidex.MultiDex
import com.lsxiao.apollo.core.Apollo
import com.orhanobut.logger.AndroidLogAdapter
import com.orhanobut.logger.Logger
import com.seaway.smallutils.SmallUtils
import com.seaway.smallutils.TtsUtils
import com.seaway.guideassistant.ble.GlassesManager
import com.seaway.guideassistant.module.httpModule
import com.seaway.guideassistant.module.robotModule
import com.tencent.mmkv.MMKV
import io.reactivex.android.schedulers.AndroidSchedulers
import org.koin.core.context.startKoin


/**
 * Date: 2020/1/4
 * author: SmallCake
 */
class MyApplication : Application() {
    companion object{
       lateinit var instance:MyApplication
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        //日志打印
        Logger.addLogAdapter(AndroidLogAdapter())
        //模块注入
        startKoin{
            modules(httpModule, robotModule)
        }
        //事件通知
        Apollo.init(AndroidSchedulers.mainThread(), this)
        //数据存储
        MMKV.initialize(this)
        //小工具初始化
        SmallUtils.init(this)
        //初始化系统TTS播报引擎
        TtsUtils.init(this)
        //初始化眼镜SDK
        GlassesManager.init(this)
    }


    //方法数量过多，合并
    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(base)
        MultiDex.install(this)
    }
}