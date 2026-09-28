package com.robonix.client

import android.app.Application
import dagger.hilt.android.HiltAndroidApp

@HiltAndroidApp
class RobonixApp : Application() {
    override fun onCreate() {
        super.onCreate()
        AppLog.init(cacheDir.resolve("logs"))
        AppLog.write("APP", "RobonixApp onCreate")
    }
}
